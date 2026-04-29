# Codegen 07 — cfp/queue.py

## File to Create
- `cfp/queue.py`

## Rule
The Redis-backed operational job queue. **Redis owns ZERO persistent business
data** (CLAUDE.md; arch.md §1 Q11). The queue is operational only — wiping
Redis loses no facts. AOF is enabled (`docker-compose.yml: redis-server
--appendonly yes`), so dead-letter and escalation queues survive restart with
≤1 s loss.

Cursor state mirrors to PostgreSQL: `cfp:cursor:{source}` is upserted to
`sites.last_cursor` after each batch. `cfp:dead` items are drained one-way
into PG by Tier 4. The queue module itself does not write to PG.

Two project-internal imports only: `config` and `cfp.prompts_parser`.

---

## Imports
```python
from __future__ import annotations
import hashlib, json, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Iterator, Optional
from urllib.parse import urlsplit, urlunsplit

import redis

from config import REDIS_URL
from cfp.prompts_parser import PromptsBundle
```

---

## Redis Key Schema

| Key pattern | Type | TTL | Purpose |
|---|---|---|---|
| `cfp:queue:tier{N}` | sorted set | none | Priority queue per tier (score = enqueue ms) |
| `cfp:inflight:<job_id>` | string JSON | 600s | Worker lease |
| `cfp:inflight:set` | set | none | All currently-leased job_ids |
| `cfp:job:<job_id>` | string JSON | 86400s | Serialized Job payload |
| `cfp:enqueued:<job_id>` | string "1" | 30 days | SETNX dedup gate |
| `cfp:dead` | list | none | Failed jobs (drained by Tier 4) |
| `cfp:escalate:tier{N}` | list | none | Awaiting next tier |
| `cfp:rate:<domain>` | string "1" | crawl_delay | Per-domain rate gate |
| `cfp:cursor:<source>` | string | none | Resume cursor; mirrored to PG |

Score formula: `score = -priority * 1e13 + enqueue_ts_ms` (higher priority sorts first).

---

## Constants

```python
DEFAULT_LEASE_SECONDS = 600
DEFAULT_DEDUP_TTL = 60 * 60 * 24 * 30
DEFAULT_JOB_TTL = 60 * 60 * 24
QUEUE_KEY_FMT = "cfp:queue:tier{tier}"
ESCALATE_KEY_FMT = "cfp:escalate:tier{tier}"
INFLIGHT_KEY_FMT = "cfp:inflight:{job_id}"
INFLIGHT_SET_KEY = "cfp:inflight:set"
JOB_KEY_FMT = "cfp:job:{job_id}"
ENQUEUED_KEY_FMT = "cfp:enqueued:{job_id}"
DEAD_KEY = "cfp:dead"
RATE_KEY_FMT = "cfp:rate:{domain}"
CURSOR_KEY_FMT = "cfp:cursor:{source}"
```

---

## Data Classes

```python
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
        d["enqueued_at"] = datetime.fromisoformat(d["enqueued_at"])
        return cls(**d)


class QueueError(RuntimeError): pass
```

`Job.metadata` keys used downstream:
- `category` (Category.value), `source_block` (category|series_index|journal_index|external)
- `letter` (A-Z), `domain`, `parent_event_id`

---

## Canonical URL + job_id (S11)

```python
def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.port and not (
        (parts.scheme == "http" and parts.port == 80) or
        (parts.scheme == "https" and parts.port == 443)
    ):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


def make_job_id(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:32]
```

---

## Connection helper

```python
_CLIENT: Optional[redis.Redis] = None

def get_redis() -> redis.Redis:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    return _CLIENT

def set_redis_for_tests(client) -> None:
    global _CLIENT
    _CLIENT = client
```

---

## Public API

```python
def enqueue_url(url, *, tier, priority=0.0, dedup=True, metadata=None) -> bool: ...
def pop_one(tier, *, lease_seconds=600) -> Optional[Job]: ...
def complete(job: Job) -> None: ...
def fail(job: Job, *, reason: str, escalate_to_tier=None) -> None: ...
def extend_lease(job: Job, *, seconds=600) -> None: ...
def dead_letter_drain() -> Iterator[Job]: ...   # non-destructive read
def enqueue_seeds(bundle: PromptsBundle) -> int: ...
def reset_inflight() -> int: ...
def set_cursor(source: str, cursor: str) -> None: ...
def get_cursor(source: str) -> Optional[str]: ...
def rate_limit_acquire(domain: str, *, crawl_delay_s: int) -> bool: ...
```

---

## Lua pop (atomic)

```lua
-- KEYS = {queue_key, inflight_set_key}; ARGV = {lease_seconds, inflight_prefix, job_prefix}
local popped = redis.call('ZPOPMIN', KEYS[1], 1)
if #popped == 0 then return nil end
local job_id = popped[1]
local job_key = ARGV[3] .. job_id
local payload = redis.call('GET', job_key)
if not payload then return nil end
redis.call('SET', ARGV[2] .. job_id, payload, 'EX', tonumber(ARGV[1]))
redis.call('SADD', KEYS[2], job_id)
return payload
```

Loaded via `script_load()` once; `evalsha` first, fallback to `eval` on NOSCRIPT.

---

## Implementation Sketch

```python
def enqueue_url(url, *, tier, priority=0.0, dedup=True, metadata=None) -> bool:
    if tier not in (1, 2, 3, 4):
        raise QueueError(f"invalid tier: {tier}")
    r = get_redis()
    job_id = make_job_id(url)
    if dedup:
        won = r.set(ENQUEUED_KEY_FMT.format(job_id=job_id), "1",
                    ex=DEFAULT_DEDUP_TTL, nx=True)
        if not won:
            return False
    job = Job(job_id=job_id, url=canonical_url(url), tier=tier,
              enqueued_at=datetime.now(timezone.utc),
              attempts=0, metadata=metadata or {})
    score = -priority * 1e13 + time.time() * 1000
    pipe = r.pipeline(transaction=True)
    pipe.set(JOB_KEY_FMT.format(job_id=job_id), job.to_json(), ex=DEFAULT_JOB_TTL)
    pipe.zadd(QUEUE_KEY_FMT.format(tier=tier), {job_id: score})
    pipe.execute()
    return True

def pop_one(tier, *, lease_seconds=600) -> Optional[Job]:
    r = get_redis()
    payload = _eval_pop(r, keys=[QUEUE_KEY_FMT.format(tier=tier), INFLIGHT_SET_KEY],
                        args=[lease_seconds, "cfp:inflight:", "cfp:job:"])
    if payload is None:
        return None
    job = Job.from_json(payload)
    job.attempts += 1
    r.set(INFLIGHT_KEY_FMT.format(job_id=job.job_id), job.to_json(),
          ex=lease_seconds, xx=True)
    return job

def complete(job) -> None:
    r = get_redis()
    pipe = r.pipeline(transaction=True)
    pipe.delete(INFLIGHT_KEY_FMT.format(job_id=job.job_id))
    pipe.srem(INFLIGHT_SET_KEY, job.job_id)
    pipe.delete(JOB_KEY_FMT.format(job_id=job.job_id))
    pipe.execute()

def fail(job, *, reason, escalate_to_tier=None) -> None:
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
        if escalate_to_tier not in (3, 4):
            raise QueueError(f"invalid escalation tier: {escalate_to_tier}")
        pipe.rpush(ESCALATE_KEY_FMT.format(tier=escalate_to_tier), serialised)
    pipe.delete(JOB_KEY_FMT.format(job_id=job.job_id))
    pipe.execute()

def enqueue_seeds(bundle) -> int:
    n = 0
    for s in bundle.seed_urls:
        meta = {"source_block": s.kind}
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
    r = get_redis()
    job_ids = list(r.smembers(INFLIGHT_SET_KEY))
    n = 0
    for job_id in job_ids:
        payload = (r.get(JOB_KEY_FMT.format(job_id=job_id))
                   or r.get(INFLIGHT_KEY_FMT.format(job_id=job_id)))
        if payload is None:
            r.srem(INFLIGHT_SET_KEY, job_id); continue
        job = Job.from_json(payload)
        score = time.time() * 1000
        pipe = r.pipeline(transaction=True)
        pipe.set(JOB_KEY_FMT.format(job_id=job.job_id), payload, ex=DEFAULT_JOB_TTL)
        pipe.zadd(QUEUE_KEY_FMT.format(tier=job.tier), {job.job_id: score})
        pipe.delete(INFLIGHT_KEY_FMT.format(job_id=job.job_id))
        pipe.srem(INFLIGHT_SET_KEY, job.job_id)
        pipe.execute()
        n += 1
    return n
```

---

## Tests (`tests/test_queue.py`)

Use `fakeredis>=2.20`. Add to requirements.txt under `# Test-only`. Integration suite uses `testcontainers.redis.RedisContainer`.

Key tests:
- enqueue dedup, canonicalisation, dedup off allows double, invalid tier raises
- pop_one lease set; empty returns None; FIFO; priority overrides FIFO
- complete clears lease; fail no-escalation lands in dead; escalation lands in escalate
- extend_lease extends TTL; after expiry raises
- lease expiry janitor pattern (reset_inflight returns 5)
- dead_letter_drain non-destructive; metadata round-trip
- job_id stable across processes (canonical URL invariance)

---

## Acceptance Criteria

- enqueue twice with dedup=True: (True, False)
- pop_one sets inflight key with TTL ≤ lease_seconds; bumps attempts
- complete removes inflight; fail no-escalate → dead; fail escalate=4 → escalate:tier4
- After lease TTL expiry simulation, reset_inflight() requeues
- enqueue_seeds returns len(seed_urls) + len(external_urls) on first run, 0 on re-run
- No psycopg, no HTTP, no Ollama

---

## Downstream Consumers

| Module | Usage |
|---|---|
| `cfp/pipeline.py` | pop_one, complete, fail, extend_lease, reset_inflight, janitor |
| `cfp/cli.py` | enqueue_seeds, reset_inflight, dead_letter_drain |
| `cfp/fetch.py` | rate_limit_acquire, get_cursor / set_cursor |
| `cfp/llm/tier*.py` | fail(escalate_to_tier=N) |

Only module that imports `redis`.
