"""Click CLI for the CFP pipeline. All commands are sync; long-running ones
drive the async pipeline via asyncio.run."""
from __future__ import annotations

import asyncio
import json as _json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click

from config import (
    CFP_MACHINE, OLLAMA_HOST, PG_DSN, PROFILE_MODELS, REDIS_URL, SEED_JSON,
)
from cfp import analytics, db, dedup, pipeline, queue
from cfp.llm.client import get_available_models, profile_intersection
from cfp.prompts_parser import load as load_prompts


def _configure_logging(verbose: bool, json_mode: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    if json_mode:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_JsonFormatter())
        logging.basicConfig(level=level, handlers=[handler], force=True)
    else:
        logging.basicConfig(
            level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            stream=sys.stderr, force=True,
        )


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return _json.dumps(payload)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="DEBUG-level logging.")
@click.option("--json", "json_mode", is_flag=True,
              help="Machine-readable JSON-line output to stderr.")
@click.pass_context
def main(ctx: click.Context, verbose: bool, json_mode: bool) -> None:
    """cfp — Conference knowledge pipeline."""
    _configure_logging(verbose, json_mode)
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_mode


@main.command("init-db")
@click.option("--seed/--no-seed", default=True,
              help="Import data/latest.json after creating schema.")
def init_db_cmd(seed: bool) -> None:
    """Create schema + extensions. Idempotent."""
    db.init_db()
    click.echo("schema ok (idempotent)", err=True)
    if seed and SEED_JSON.exists():
        with db.get_conn() as conn:
            sid = db.begin_session(conn, machine=CFP_MACHINE,
                                   git_sha="seed", prompts_md_sha="seed")
            n = db.seed_from_json(conn, SEED_JSON, sid)
            db.end_session(conn, sid)
        click.echo(f"seeded {n} events from {SEED_JSON.name}", err=True)


@main.command("enqueue-seeds")
def enqueue_seeds_cmd() -> None:
    """Parse prompts.md and push new seed URLs to cfp:queue:tier1."""
    bundle = load_prompts()
    n = queue.enqueue_seeds(bundle)
    click.echo(f"enqueued {n} new seed urls", err=True)


@main.command("run-pipeline")
@click.option("--workers", "-w", type=int, default=1, show_default=True,
              help="Number of concurrent tier-1 workers.")
@click.option("--tier2-workers", type=int, default=1, show_default=True)
@click.option("--tier1-only", is_flag=True)
@click.option("--tier2-only", is_flag=True)
def run_pipeline_cmd(workers: int, tier2_workers: int,
                     tier1_only: bool, tier2_only: bool) -> None:
    """Long-running: fetch → parse → tier1 → tier2 → DB."""
    if tier1_only and tier2_only:
        raise click.UsageError("--tier1-only and --tier2-only are exclusive")

    async def _run() -> None:
        coros = []
        if not tier2_only:
            coros.append(pipeline.run_workers(workers))
        if not tier1_only:
            coros.append(pipeline.run_tier2_workers(tier2_workers))
        await asyncio.gather(*coros)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        click.echo("interrupted; in-flight jobs returned to queue", err=True)
        sys.exit(130)


@main.command("generate-reports")
@click.option("--reports-dir", type=click.Path(file_okay=False), default=None)
def generate_reports_cmd(reports_dir: Optional[str]) -> None:
    """Generate Markdown reports from PostgreSQL state."""
    n = analytics.generate_all(
        reports_dir=Path(reports_dir) if reports_dir else None
    )
    click.echo(f"reports written ({n} events)", err=True)


@main.command("dedup-sweep")
def dedup_sweep_cmd() -> None:
    """Run pgvector + acronym dedup sweep across all unsuperseded events."""
    report = dedup.sweep()
    click.echo(
        f"dedup-sweep done — examined={report.pairs_examined}, "
        f"merged={report.merges_applied}, queued={report.pairs_queued_for_llm}",
        err=True,
    )


@main.command("bootstrap-ontology")
def bootstrap_ontology_cmd() -> None:
    """Bootstrap ontology graph. v2 only; no-op in v1."""
    click.echo("bootstrap-ontology is v2 only (requires Apache AGE)", err=True)


@main.command("list-models")
def list_models_cmd() -> None:
    """Print space-separated models for the current CFP_MACHINE.

    Used by setup.sh: ollama pull $(python -m cfp list-models)"""
    sys.stdout.write(" ".join(PROFILE_MODELS.get(CFP_MACHINE, [])) + "\n")


@main.command("doctor")
@click.option("--all", "show_all", is_flag=True,
              help="Run every check; report all failures.")
def doctor_cmd(show_all: bool) -> None:
    """End-to-end health check: PG, Redis, Ollama, models, queue depths."""
    checks: list[tuple[str, callable]] = [
        ("postgres", _check_postgres),
        ("redis",    _check_redis),
        ("ollama",   _check_ollama),
        ("models",   _check_models),
        ("queue",    _check_queue_depths),
    ]
    failed: list[str] = []
    rows: list[tuple[str, str, str]] = []
    for name, fn in checks:
        ok, detail = fn()
        rows.append((name, "OK" if ok else "FAIL", detail))
        if not ok:
            failed.append(f"{name}: {detail}")
            if not show_all:
                _print_doctor_table(rows)
                click.echo(f"doctor failed: {failed[0]}", err=True)
                sys.exit(1)
    _print_doctor_table(rows)
    if failed:
        for f in failed:
            click.echo(f"FAIL {f}", err=True)
        sys.exit(1)
    click.echo("all checks passed", err=True)


def _print_doctor_table(rows: list[tuple[str, str, str]]) -> None:
    width = max(len(r[0]) for r in rows) + 2
    for name, status, detail in rows:
        click.echo(f"  {name:<{width}} {status:<5} {detail}")


def _check_postgres() -> tuple[bool, str]:
    try:
        with db.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True, PG_DSN
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _check_redis() -> tuple[bool, str]:
    try:
        ok = queue.get_redis().ping()
        return bool(ok), REDIS_URL
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _check_ollama() -> tuple[bool, str]:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{OLLAMA_HOST}/api/tags", timeout=5) as r:
            return r.status == 200, OLLAMA_HOST
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _check_models() -> tuple[bool, str]:
    needed = set(PROFILE_MODELS.get(CFP_MACHINE, []))
    try:
        have = set(get_available_models())
    except Exception as e:
        return False, f"ollama unreachable: {e}"
    # Allow have to include "model:latest" for "model" entries
    matched = {n for n in needed if n in have or
               any(h.startswith(n + ":") for h in have) or
               any(h == n + ":latest" for h in have)}
    missing = needed - matched
    if missing:
        return False, f"missing: {sorted(missing)}"
    return True, f"{len(needed)} models present"


def _check_queue_depths() -> tuple[bool, str]:
    try:
        r = queue.get_redis()
        depths = {
            "tier1":          r.zcard("cfp:queue:tier1"),
            "tier2":          r.llen("cfp:queue:tier2"),
            "escalate_tier3": r.llen("cfp:escalate:tier3"),
            "escalate_tier4": r.llen("cfp:escalate:tier4"),
            "dead":           r.llen("cfp:dead"),
            "inflight":       r.scard("cfp:inflight:set"),
        }
        msg = " ".join(f"{k}={v}" for k, v in depths.items())
        return True, msg
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
