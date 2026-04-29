# Codegen 12 — cfp/pipeline.py + cfp/cli.py

## Files to Create
- `cfp/pipeline.py`
- `cfp/cli.py`
- `cfp/__main__.py` (one-liner: `from cfp.cli import main; main()`)

## Rule
Orchestration layer. Every other module is imported here and stitched
together. **No business logic invented** — all decisions trace to existing
modules.

`pipeline.py` is async; `cli.py` is sync Click that drives it via `asyncio.run`.

Per arch.md §1 Q5: when CFP_MACHINE lacks Tier 3/4 models, workers SKIP that
tier and push to `cfp:escalate:tier{N}`. CLI `doctor` reports queue depths.

---

## pipeline.py — Imports

```python
from __future__ import annotations
import asyncio, json, logging, os, signal, socket, time, uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from redis import asyncio as aioredis

from config import (CFP_MACHINE, DEAD_LETTER_KEY, OLLAMA_HOST, PG_DSN,
                    PROFILE_MODELS, REDIS_URL, TIER_THRESHOLD, USER_AGENT)
from cfp import db, fetch, queue
from cfp.llm.client import profile_intersection
from cfp.llm import tier1 as tier1_runner
from cfp.llm import tier2 as tier2_runner
from cfp.models import Event, JobStatus, ScrapeJob, Tier, TierResult
from cfp.parsers import dispatch as parser_dispatch
from cfp.prompts_parser import load as load_prompts
```

Tier 3/4 imported lazily inside the worker closure.

---

## App context (lifecycle)

```python
class _AppContext:
    __slots__ = ("session", "redis", "shutdown", "available_models",
                 "session_id", "tier3_runner", "tier4_runner")

@asynccontextmanager
async def _app_context():
    ctx = _AppContext()
    ctx.session = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30, connect=10),
        headers={"User-Agent": USER_AGENT})
    ctx.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    ctx.shutdown = asyncio.Event()
    ctx.available_models = set(profile_intersection())

    # Lazy-import t3/t4 only if profile has the model
    if any(m.startswith("qwen3:32b") for m in ctx.available_models):
        from cfp.llm import tier3 as t3
        ctx.tier3_runner = t3
    else:
        ctx.tier3_runner = None
    if any(m.startswith("deepseek-r1") for m in ctx.available_models):
        from cfp.llm import tier4 as t4
        ctx.tier4_runner = t4
    else:
        ctx.tier4_runner = None

    with db.get_conn() as conn:
        ctx.session_id = db.begin_session(conn, machine=CFP_MACHINE,
            git_sha=os.getenv("GIT_SHA", "unknown"),
            prompts_md_sha=_prompts_md_sha())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, ctx.shutdown.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: ctx.shutdown.set())

    try:
        yield ctx
    finally:
        await ctx.session.close()
        await ctx.redis.aclose()
        with db.get_conn() as conn:
            db.end_session(conn, ctx.session_id)
```

---

## run_one_job

```python
async def run_one_job(job: ScrapeJob, ctx: _AppContext) -> None:
    t0 = time.perf_counter()
    job_id = job.url
    try:
        html = await fetch.get_text(job.url)
        if html is None:
            await queue.dead_letter(job_id, "fetch_failed", {"url": job.url})
            return

        # Parse via dispatch (rule-based for known domains)
        candidates: list[Event] = []
        enrichment = parser_dispatch(job.url, html)
        if enrichment is None:
            payload = {"url": job.url, "html": html[:50_000]}
            await queue.escalate(payload, tier=3, reason="unknown_site")
            await queue.metrics_incr(1, "escalated")
            return

        # Tier 1 per candidate
        for ev in candidates:
            result = await tier1_runner.run_tier1(ev, ctx.session)
            with db.get_conn() as conn:
                _record_tier_run(conn, ev, result, ctx.session_id)
            if result.escalate or result.confidence < TIER_THRESHOLD[1]:
                await queue.escalate({"event": asdict(ev), "tier1": asdict(result)},
                                     tier=2, reason=result.escalate_reason or "low_confidence")
                await queue.metrics_incr(1, "escalated")
            else:
                await queue.push_tier2({"event": asdict(ev), "tier1": asdict(result)})
                await queue.metrics_incr(1, "ok")
        await queue.ack(job_id)
    except asyncio.CancelledError:
        await queue.nack(job_id)
        raise
    except Exception as e:
        logging.exception("run_one_job failed url=%s", job.url)
        await queue.dead_letter(job_id, f"{type(e).__name__}: {e}",
                                {"url": job.url})
        await queue.metrics_incr(1, "failed")
```

---

## run_workers (and run_tier2_workers)

```python
async def run_workers(n: int = 1) -> None:
    async with _app_context() as ctx:
        tasks = [asyncio.create_task(_tier1_worker(i, ctx), name=f"tier1-{i}")
                 for i in range(n)]
        tasks.append(asyncio.create_task(_janitor(ctx), name="janitor"))
        await ctx.shutdown.wait()
        for t in tasks: t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

async def _tier1_worker(idx, ctx):
    while not ctx.shutdown.is_set():
        try:
            job = await queue.pop_tier1(timeout=5.0)
        except asyncio.CancelledError:
            return
        except Exception:
            logging.exception("tier1 pop failed"); await asyncio.sleep(2); continue
        if job is None: continue
        await run_one_job(job, ctx)
```

Janitor: every 60s reset expired leases via `queue.reset_expired_leases()`.

---

## cli.py — Click commands

```python
@click.group()
@click.option("--verbose", "-v", is_flag=True)
@click.option("--json", "json_mode", is_flag=True)
@click.pass_context
def main(ctx, verbose, json_mode):
    """cfp — Conference knowledge pipeline CLI."""
    _configure_logging(verbose, json_mode)

@main.command("init-db")
@click.option("--seed/--no-seed", default=True)
def init_db_cmd(seed):
    db.init_db()
    if seed and SEED_JSON.exists():
        with db.get_conn() as conn:
            sid = db.begin_session(conn, machine=CFP_MACHINE, ...)
            n = db.seed_from_json(conn, SEED_JSON, sid)
            db.end_session(conn, sid)

@main.command("enqueue-seeds")
def enqueue_seeds_cmd():
    bundle = load_prompts()
    n = asyncio.run(queue.enqueue_seeds(bundle))
    console.print(f"enqueued {n} new seed urls")

@main.command("run-pipeline")
@click.option("--workers", "-w", default=1)
@click.option("--tier2-workers", default=1)
@click.option("--tier1-only", is_flag=True)
@click.option("--tier2-only", is_flag=True)
def run_pipeline_cmd(workers, tier2_workers, tier1_only, tier2_only):
    async def _run():
        coros = []
        if not tier2_only: coros.append(pipeline.run_workers(workers))
        if not tier1_only: coros.append(pipeline.run_tier2_workers(tier2_workers))
        await asyncio.gather(*coros)
    try: asyncio.run(_run())
    except KeyboardInterrupt: sys.exit(130)

@main.command("generate-reports")
def generate_reports_cmd():
    from generate_md import generate_all
    generate_all()

@main.command("dedup-sweep")
def dedup_sweep_cmd():
    from cfp import dedup as _d
    n = _d.sweep()
    console.print(f"dedup sweep done — {n} pairs evaluated")

@main.command("bootstrap-ontology")
def bootstrap_ontology_cmd():
    console.print("[yellow]bootstrap-ontology is v2 only[/yellow]")
    sys.exit(0)

@main.command("list-models")
def list_models_cmd():
    """For setup.sh: ollama pull $(python -m cfp list-models)"""
    sys.stdout.write(" ".join(PROFILE_MODELS.get(CFP_MACHINE, [])) + "\n")

@main.command("doctor")
@click.option("--all", "show_all", is_flag=True)
def doctor_cmd(show_all):
    """Health check: postgres, redis, ollama, models, queue depths."""
    checks = [("postgres", _check_postgres), ("redis", _check_redis),
              ("ollama", _check_ollama), ("models", _check_models),
              ("queue", _check_queue_depths)]
    failed = []
    for name, fn in checks:
        ok, detail = fn()
        if not ok: failed.append(f"{name}: {detail}")
        if not ok and not show_all: sys.exit(1)
    if failed: sys.exit(1)
```

### Doctor checks
```python
def _check_postgres():
    try:
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1"); cur.fetchone()
        return True, PG_DSN
    except Exception as e: return False, f"{type(e).__name__}: {e}"

def _check_redis():
    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        return bool(asyncio.run(r.ping())), REDIS_URL
    except Exception as e: return False, str(e)

def _check_ollama():
    import urllib.request
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=5) as r:
            return r.status == 200, OLLAMA_HOST
    except Exception as e: return False, str(e)

def _check_models():
    needed = set(PROFILE_MODELS.get(CFP_MACHINE, []))
    try: have = set(get_available_models())
    except Exception as e: return False, f"ollama: {e}"
    missing = needed - have
    return (not missing, f"missing: {sorted(missing)}" if missing
                         else f"{len(needed)} models present")
```

---

## Tests (`tests/test_pipeline_cli.py`)

- doctor all healthy → exit 0
- doctor one service down → exit 1 with reason
- list-models outputs space-separated for CFP_MACHINE=gpu_mid
- init-db idempotent (run twice, second exits 0)
- enqueue-seeds dedup: second run returns 0 new urls
- SIGINT during in-flight job → job goes back to queue (NACK), not dead-letter

---

## Acceptance Criteria

- `python -m cfp init-db` — schema created idempotently
- `python -m cfp enqueue-seeds` — populates cfp:queue:tier1; second run = 0
- `python -m cfp run-pipeline --workers N` — N tier1 + 1 tier2 + janitor; SIGINT → NACK
- `python -m cfp doctor` — exits 0 healthy, 1 with reason
- `python -m cfp list-models` — single space-separated stdout line
- Per arch.md §1 Q5: missing local model → push to cfp:escalate:tierN, no error

---

## Downstream Consumers

| Caller | Usage |
|---|---|
| `setup.sh` | `python -m cfp list-models` for ollama pull |
| `Makefile` | every CLI command as a target |
| Operator | `python -m cfp doctor` |

`pipeline.py` imported only by `cli.py` and tests.
