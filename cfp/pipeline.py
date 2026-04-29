"""Async pipeline orchestrator. Workers pop jobs from Redis queues, fetch +
parse + tier them, and write results to PostgreSQL via cfp.db. Lifecycle is
managed via _app_context which holds the shared aiohttp + Redis pools and a
shutdown event triggered by SIGINT/SIGTERM."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from contextlib import asynccontextmanager
from typing import Optional

from config import (
    CFP_MACHINE,
    PROFILE_MODELS,
    TIER_THRESHOLD,
)
from cfp import db, fetch, queue
from cfp.llm import TierEscalation, tier1 as tier1_runner, tier2 as tier2_runner
from cfp.parsers import dispatch as parser_dispatch

log = logging.getLogger("cfp.pipeline")


class _AppContext:
    """Lifecycle holder for one pipeline run."""
    __slots__ = ("shutdown", "session_id")

    def __init__(self) -> None:
        self.shutdown: asyncio.Event = asyncio.Event()
        self.session_id: str = ""


@asynccontextmanager
async def _app_context():
    ctx = _AppContext()

    with db.get_conn() as conn:
        ctx.session_id = db.begin_session(
            conn,
            machine=CFP_MACHINE,
            git_sha=os.getenv("GIT_SHA", "unknown"),
            prompts_md_sha=_prompts_md_sha(),
        )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, ctx.shutdown.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: ctx.shutdown.set())

    try:
        yield ctx
    finally:
        await fetch.close()
        with db.get_conn() as conn:
            db.end_session(conn, ctx.session_id)


def _prompts_md_sha() -> str:
    import hashlib
    from config import PROMPTS_FILE
    return hashlib.sha1(PROMPTS_FILE.read_bytes()).hexdigest()[:12]


async def run_one_job(job: queue.Job, ctx: _AppContext) -> None:
    """Single Tier-1 worker iteration: fetch → parse → tier1."""
    try:
        text = await fetch.get_text(job.url)
    except fetch.FetchPermanentError as e:
        log.warning("permanent fetch failure on %s: %s", job.url, e)
        queue.fail(job, reason=f"fetch_permanent: {e}")
        return
    except fetch.FetchRobotsBlocked as e:
        log.warning("robots blocked %s: %s", job.url, e)
        queue.fail(job, reason="robots_blocked")
        return
    except fetch.FetchTimeoutError as e:
        log.warning("fetch timeout on %s: %s — re-enqueueing", job.url, e)
        queue.fail(job, reason=f"fetch_timeout: {e}")
        return

    parsed = parser_dispatch(job.url, text)
    if parsed is None:
        log.info("no parser for %s — pushing to escalate:tier3", job.url)
        queue.fail(job, reason="unknown_site", escalate_to_tier=3)
        return

    candidates = parsed if isinstance(parsed, list) else [parsed]
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        snippet = cand.get("description") or cand.get("name") or job.url
        try:
            await tier1_runner.run_tier1(
                snippet, source_url=job.url,
                scrape_session_id=ctx.session_id,
            )
        except TierEscalation as e:
            log.info("tier1 escalation: %s", e)
        except Exception:
            log.exception("tier1 unhandled exception on %s", job.url)

    queue.complete(job)


async def run_one_tier2_job(payload: dict, ctx: _AppContext) -> None:
    """Tier-2 worker iteration: full extraction + COALESCE upsert + embedding."""
    event_id = int(payload.get("event_id") or -1)
    text = payload.get("text") or ""
    if event_id < 0 or not text:
        log.warning("malformed tier2 payload: %r", payload)
        return
    try:
        await tier2_runner.run_tier2(
            event_id, text, scrape_session_id=ctx.session_id,
        )
    except TierEscalation as e:
        log.info("tier2 escalation event_id=%d: %s", event_id, e)
    except Exception:
        log.exception("tier2 unhandled exception event_id=%d", event_id)


async def _tier1_worker(idx: int, ctx: _AppContext) -> None:
    while not ctx.shutdown.is_set():
        try:
            job = await asyncio.to_thread(queue.pop_one, 1)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("tier1 worker[%d] pop failed", idx)
            await asyncio.sleep(2)
            continue
        if job is None:
            await asyncio.sleep(0.5)
            continue
        await run_one_job(job, ctx)


async def _tier2_worker(idx: int, ctx: _AppContext) -> None:
    """Tier 2 payloads land on cfp:queue:tier2 as raw JSON via push_tier2."""
    redis_client = queue.get_redis()
    while not ctx.shutdown.is_set():
        try:
            raw = await asyncio.to_thread(
                redis_client.lpop, queue.QUEUE_KEY_FMT.format(tier=2)
            )
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("tier2 worker[%d] pop failed", idx)
            await asyncio.sleep(2)
            continue
        if not raw:
            await asyncio.sleep(0.5)
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("tier2 dropping malformed payload")
            continue
        await run_one_tier2_job(payload, ctx)


async def _janitor(ctx: _AppContext) -> None:
    """Reset expired inflight leases every 60s."""
    while not ctx.shutdown.is_set():
        try:
            n = await asyncio.to_thread(queue.reset_inflight)
            if n:
                log.info("janitor reset %d expired leases", n)
        except Exception:
            log.exception("janitor cycle failed")
        try:
            await asyncio.wait_for(ctx.shutdown.wait(), timeout=60)
        except asyncio.TimeoutError:
            continue


async def run_workers(n: int = 1) -> None:
    """Spawn N Tier-1 workers + janitor; wait until shutdown."""
    async with _app_context() as ctx:
        tasks = [
            asyncio.create_task(_tier1_worker(i, ctx), name=f"tier1-{i}")
            for i in range(n)
        ]
        tasks.append(asyncio.create_task(_janitor(ctx), name="janitor"))
        log.info("started %d tier1 workers; pid=%d", n, os.getpid())
        await ctx.shutdown.wait()
        log.info("shutdown signalled; cancelling workers")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run_tier2_workers(n: int = 1) -> None:
    async with _app_context() as ctx:
        tasks = [
            asyncio.create_task(_tier2_worker(i, ctx), name=f"tier2-{i}")
            for i in range(n)
        ]
        tasks.append(asyncio.create_task(_janitor(ctx), name="janitor"))
        log.info("started %d tier2 workers", n)
        await ctx.shutdown.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
