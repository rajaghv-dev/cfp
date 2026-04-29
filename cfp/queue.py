"""Redis-backed operational job queue for the CFP pipeline.

Redis owns ZERO persistent business data (CLAUDE.md; arch.md §1 Q11).
Wiping Redis loses no facts. AOF is enabled, so dead-letter and escalation
queues survive restart with <=1s loss.

Only module that imports `redis`.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit, urlunsplit

import redis
from redis.exceptions import NoScriptError, ResponseError

from config import REDIS_URL
from cfp.prompts_parser import PromptsBundle


DEFAULT_LEASE_SECONDS = 600
DEFAULT_DEDUP_TTL     = 60 * 60 * 24 * 30   # 30 days
DEFAULT_JOB_TTL       = 60 * 60 * 24        # 24 h

QUEUE_KEY_FMT     = "cfp:queue:tier{tier}"
ESCALATE_KEY_FMT  = "cfp:escalate:tier{tier}"
INFLIGHT_KEY_FMT  = "cfp:inflight:{job_id}"
INFLIGHT_SET_KEY  = "cfp:inflight:set"
JOB_KEY_FMT       = "cfp:job:{job_id}"
ENQUEUED_KEY_FMT  = "cfp:enqueued:{job_id}"
DEAD_KEY          = "cfp:dead"
RATE_KEY_FMT      = "cfp:rate:{domain}"
CURSOR_KEY_FMT    = "cfp:cursor:{source}"

_VALID_TIERS = (1, 2, 3, 4)
_VALID_ESCALATION_TIERS = (3, 4)


# ---------------------------------------------------------------------------
# Data classes


@dataclass(slots=True)
class Job:
    job_id: str
    url: str
    tier: int
    enqueued_at: datetime
    attempts: int = 0
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        d = asdict(self)
        d["enqueued_at"] = self.enqueued_at.isoformat()
        return json.dumps(d, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "Job":
        d = json.loads(raw)
        ts = d["enqueued_at"]
        # fromisoformat handles 'Z' only on 3.11+; our minimum is 3.12 (venv).
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        d["enqueued_at"] = dt
        return cls(**d)


class QueueError(RuntimeError):
    """Queue invariant violation (invalid tier, expired lease, etc.)."""


# ---------------------------------------------------------------------------
# Canonicalisation


def canonical_url(url: str) -> str:
    """Stable form for hashing: lowercase scheme/host, drop default ports,
    drop trailing slash on non-root paths, drop fragment, keep query."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if parts.port and not (
        (scheme == "http" and parts.port == 80)
        or (scheme == "https" and parts.port == 443)
    ):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((scheme, host, path, parts.query, ""))


def make_job_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Connection helper (test seam: set_redis_for_tests)


_CLIENT: Optional[redis.Redis] = None
_POP_SHA: Optional[str] = None


def get_redis() -> redis.Redis:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _CLIENT


def set_redis_for_tests(client) -> None:
    """Inject a fakeredis (or any redis-compatible) client for tests."""
    global _CLIENT, _POP_SHA
    _CLIENT = client
    _POP_SHA = None


# ---------------------------------------------------------------------------
# Lua: atomic ZPOPMIN + GET payload + SET inflight + SADD inflight set


_LUA_POP = """
local popped = redis.call('ZPOPMIN', KEYS[1], 1)
if #popped == 0 then return nil end
local job_id = popped[1]
local job_key = ARGV[3] .. job_id
local payload = redis.call('GET', job_key)
if not payload then return nil end
redis.call('SET', ARGV[2] .. job_id, payload, 'EX', tonumber(ARGV[1]))
redis.call('SADD', KEYS[2], job_id)
return payload
"""


def _ensure_pop_sha(r: redis.Redis) -> Optional[str]:
    global _POP_SHA
    if _POP_SHA is None:
        try:
            _POP_SHA = r.script_load(_LUA_POP)
        except (ResponseError, redis.RedisError, NotImplementedError):
            _POP_SHA = None
    return _POP_SHA


def _eval_pop(r: redis.Redis, *, keys: list[str], args: list[Any]) -> Optional[str]:
    """Run the atomic pop. Try EVALSHA, fall back to EVAL on NOSCRIPT,
    fall back to a non-atomic Python implementation if Lua is unsupported
    (e.g. a fakeredis build without Lua)."""
    sha = _ensure_pop_sha(r)
    try:
        if sha is not None:
            try:
                return r.evalsha(sha, len(keys), *keys, *args)
            except NoScriptError:
                # script flushed — reload via EVAL (server caches it again)
                return r.eval(_LUA_POP, len(keys), *keys, *args)
        return r.eval(_LUA_POP, len(keys), *keys, *args)
    except (ResponseError, redis.RedisError, NotImplementedError):
        return _python_pop(r, keys=keys, args=args)


def _python_pop(r: redis.Redis, *, keys: list[str], args: list[Any]) -> Optional[str]:
    """Non-atomic fallback for environments lacking Lua (some fakeredis builds)."""
    queue_key, inflight_set_key = keys
    lease_seconds, inflight_prefix, job_prefix = args
    popped = r.zpopmin(queue_key, 1)
    if not popped:
        return None
    job_id, _score = popped[0]
    payload = r.get(job_prefix + job_id)
    if payload is None:
        return None
    r.set(inflight_prefix + job_id, payload, ex=int(lease_seconds))
    r.sadd(inflight_set_key, job_id)
    return payload


# ---------------------------------------------------------------------------
# Public API


def enqueue_url(
    url: str,
    *,
    tier: int,
    priority: float = 0.0,
    dedup: bool = True,
    metadata: Optional[dict] = None,
) -> bool:
    """Enqueue url to cfp:queue:tier{tier}. Returns True if newly enqueued,
    False if dedup gate rejected. Raises QueueError on invalid tier."""
    if tier not in _VALID_TIERS:
        raise QueueError(f"invalid tier: {tier}")
    r = get_redis()
    job_id = make_job_id(url)
    if dedup:
        won = r.set(
            ENQUEUED_KEY_FMT.format(job_id=job_id),
            "1",
            ex=DEFAULT_DEDUP_TTL,
            nx=True,
        )
        if not won:
            return False
    job = Job(
        job_id=job_id,
        url=canonical_url(url),
        tier=tier,
        enqueued_at=datetime.now(timezone.utc),
        attempts=0,
        metadata=metadata or {},
    )
    # Higher priority sorts first: score = -priority * 1e13 + ms_ts.
    score = -priority * 1e13 + time.time() * 1000
    pipe = r.pipeline(transaction=True)
    pipe.set(JOB_KEY_FMT.format(job_id=job_id), job.to_json(), ex=DEFAULT_JOB_TTL)
    pipe.zadd(QUEUE_KEY_FMT.format(tier=tier), {job_id: score})
    pipe.execute()
    return True


def pop_one(tier: int, *, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> Optional[Job]:
    if tier not in _VALID_TIERS:
        raise QueueError(f"invalid tier: {tier}")
    r = get_redis()
    payload = _eval_pop(
        r,
        keys=[QUEUE_KEY_FMT.format(tier=tier), INFLIGHT_SET_KEY],
        args=[lease_seconds, "cfp:inflight:", "cfp:job:"],
    )
    if payload is None:
        return None
    job = Job.from_json(payload)
    job.attempts += 1
    # Refresh inflight payload with the bumped attempts; xx=True keeps the
    # same TTL window the Lua script just established (no extension here).
    r.set(
        INFLIGHT_KEY_FMT.format(job_id=job.job_id),
        job.to_json(),
        ex=lease_seconds,
        xx=True,
    )
    return job


def complete(job: Job) -> None:
    r = get_redis()
    pipe = r.pipeline(transaction=True)
    pipe.delete(INFLIGHT_KEY_FMT.format(job_id=job.job_id))
    pipe.srem(INFLIGHT_SET_KEY, job.job_id)
    pipe.delete(JOB_KEY_FMT.format(job_id=job.job_id))
    pipe.execute()


def fail(
    job: Job,
    *,
    reason: str,
    escalate_to_tier: Optional[int] = None,
) -> None:
    if escalate_to_tier is not None and escalate_to_tier not in _VALID_ESCALATION_TIERS:
        raise QueueError(f"invalid escalation tier: {escalate_to_tier}")
    r = get_redis()
    payload = json.loads(job.to_json())
    payload["failure_reason"] = reason
    serialised = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    pipe = r.pipeline(transaction=True)
    pipe.delete(INFLIGHT_KEY_FMT.format(job_id=job.job_id))
    pipe.srem(INFLIGHT_SET_KEY, job.job_id)
    if escalate_to_tier is None:
        pipe.rpush(DEAD_KEY, serialised)
    else:
        pipe.rpush(ESCALATE_KEY_FMT.format(tier=escalate_to_tier), serialised)
    pipe.delete(JOB_KEY_FMT.format(job_id=job.job_id))
    pipe.execute()


def extend_lease(job: Job, *, seconds: int = DEFAULT_LEASE_SECONDS) -> None:
    """Extend the inflight lease by `seconds`. Raises QueueError if the
    lease has already expired (key missing)."""
    r = get_redis()
    key = INFLIGHT_KEY_FMT.format(job_id=job.job_id)
    if not r.expire(key, seconds):
        raise QueueError(f"lease expired or missing for job {job.job_id}")


def dead_letter_drain() -> Iterator[Job]:
    """Iterate the dead-letter list non-destructively. Tier 4 / operator
    code reads, then explicitly LPOPs after PG has accepted the row."""
    r = get_redis()
    length = r.llen(DEAD_KEY)
    for i in range(length):
        raw = r.lindex(DEAD_KEY, i)
        if raw is None:
            continue
        # Failure payload includes 'failure_reason' which is not a Job field.
        d = json.loads(raw)
        d.pop("failure_reason", None)
        yield Job.from_json(json.dumps(d))


def enqueue_seeds(bundle: PromptsBundle) -> int:
    """Route prompts.md seed_urls + external_urls into cfp:queue:tier1.
    Returns the number of *new* enqueues (dedup-aware: idempotent on re-run)."""
    n = 0
    for s in bundle.seed_urls:
        meta: dict = {"source_block": s.kind}
        if s.category is not None:
            meta["category"] = s.category.value
        if s.letter is not None:
            meta["letter"] = s.letter
        if enqueue_url(s.url, tier=1, metadata=meta):
            n += 1
    for u in bundle.external_urls:
        if enqueue_url(u, tier=1, metadata={"source_block": "external"}):
            n += 1
    return n


def reset_inflight() -> int:
    """Janitor: re-queue every job currently in the inflight set.
    Used at worker startup and after a crash. Returns count requeued."""
    r = get_redis()
    job_ids = list(r.smembers(INFLIGHT_SET_KEY))
    n = 0
    for job_id in job_ids:
        payload = r.get(JOB_KEY_FMT.format(job_id=job_id)) or r.get(
            INFLIGHT_KEY_FMT.format(job_id=job_id)
        )
        if payload is None:
            r.srem(INFLIGHT_SET_KEY, job_id)
            continue
        try:
            job = Job.from_json(payload)
        except (ValueError, KeyError):
            r.srem(INFLIGHT_SET_KEY, job_id)
            continue
        score = time.time() * 1000
        pipe = r.pipeline(transaction=True)
        pipe.set(
            JOB_KEY_FMT.format(job_id=job.job_id),
            payload,
            ex=DEFAULT_JOB_TTL,
        )
        pipe.zadd(QUEUE_KEY_FMT.format(tier=job.tier), {job.job_id: score})
        pipe.delete(INFLIGHT_KEY_FMT.format(job_id=job.job_id))
        pipe.srem(INFLIGHT_SET_KEY, job.job_id)
        pipe.execute()
        n += 1
    return n


def set_cursor(source: str, cursor: str) -> None:
    """Mirror cursor to Redis. Caller is responsible for the PG upsert."""
    get_redis().set(CURSOR_KEY_FMT.format(source=source), cursor)


def get_cursor(source: str) -> Optional[str]:
    return get_redis().get(CURSOR_KEY_FMT.format(source=source))


def rate_limit_acquire(domain: str, *, crawl_delay_s: int) -> bool:
    """Per-domain crawl gate. SETNX with TTL = crawl_delay_s.
    Returns True if caller may proceed, False if a fetch happened recently."""
    r = get_redis()
    won = r.set(
        RATE_KEY_FMT.format(domain=domain.lower()),
        "1",
        ex=max(1, int(crawl_delay_s)),
        nx=True,
    )
    return bool(won)
