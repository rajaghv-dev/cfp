# Codegen 03 — cfp/fetch.py

## File to Create
- `cfp/fetch.py`

## Rule
HTTP fetch layer for the CFP scraper. **Async** (`aiohttp`), not `requests` —
per `arch.md §S13` the per-domain rate limit + 3–5× throughput gain matters
with ~5,680 first-run pages on WikiCFP.

This module owns:
- one shared `aiohttp.ClientSession` per process (lazy init, connection pooled)
- per-domain Gaussian human-delay + occasional "reading" pause
- per-domain `asyncio.Lock` (process-wide rate limiter dict)
- robots.txt parsing + caching per session
- exponential backoff on 429/5xx, immediate raise on 4xx
- typed exceptions consumed by `cfp/queue.py` and `cfp/pipeline.py`

It does NOT touch the DB, Redis, the LLM, or the parsers. It returns raw
`bytes`; parsers handle decoding (charset detection lives in the parser layer).

> **Note on Redis rate limiter.** `context.md §8` mentions a Redis
> `cfp:rate:{domain}` SETNX gate. That gate is the *cross-process* enforcer
> (set in `cfp/queue.py` before dequeue). The `asyncio.Lock` here is the
> *intra-process* enforcer for concurrent coroutines in the same worker.

---

## Imports
```python
from __future__ import annotations
import asyncio, logging, random, time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import aiohttp
from aiohttp import ClientResponseError, ClientError

from config import (
    USER_AGENT,
    HUMAN_DELAY_MEAN, HUMAN_DELAY_STD, HUMAN_DELAY_MIN, HUMAN_DELAY_MAX,
    HUMAN_DELAY_LONG_PROB,
    MAX_RETRIES, RETRY_BACKOFF_BASE, RETRY_BACKOFF_CAP,
)

log = logging.getLogger(__name__)
```

No project-internal imports beyond `config`.

---

## Constants

```python
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
_READING_PAUSE_MIN = 15.0
_READING_PAUSE_MAX = 45.0
_ROBOTS_TIMEOUT = aiohttp.ClientTimeout(total=10)
```

---

## Public API

```python
async def get(url: str, *, headers: Optional[dict] = None,
              params: Optional[dict] = None) -> bytes: ...
    """Returns raw bytes on 2xx. Raises FetchRobotsBlocked, FetchPermanentError, FetchTimeoutError."""

async def get_text(url: str, *, headers=None, params=None) -> str: ...
    """get() + utf-8 decode with errors='replace'."""

async def head(url: str, *, headers=None) -> int: ...
    """HEAD; returns status code as int (no 4xx raise). Subject to robots + rate limit."""

async def close() -> None: ...
    """Close shared ClientSession. Idempotent."""


class FetchError(Exception):
    """Base."""

class FetchPermanentError(FetchError):
    """4xx other than 429."""
    def __init__(self, url, status, message=""):
        self.url, self.status = url, status
        super().__init__(f"{status} on {url}: {message}")

class FetchTimeoutError(FetchError):
    """Exhausted MAX_RETRIES on 429/5xx/network."""

class FetchRobotsBlocked(FetchError):
    """robots.txt disallows."""
```

---

## Module-level state

```python
_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()
_domain_locks: dict[str, asyncio.Lock] = {}
_domain_locks_guard = asyncio.Lock()
_robots_cache: dict[str, RobotFileParser] = {}
_robots_cache_guard = asyncio.Lock()
```

---

## Implementation Sketch

### Session bootstrap

```python
async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                connector = aiohttp.TCPConnector(limit=20, limit_per_host=4, ttl_dns_cache=300)
                _session = aiohttp.ClientSession(
                    connector=connector, timeout=_REQUEST_TIMEOUT,
                    headers={"User-Agent": USER_AGENT})
    return _session

async def close() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None
```

### Domain extraction
```python
def _domain(url: str) -> str:
    p = urlparse(url)
    return (p.hostname or "").lower()
```

### Per-domain lock
```python
async def _lock_for(domain: str) -> asyncio.Lock:
    async with _domain_locks_guard:
        lock = _domain_locks.get(domain)
        if lock is None:
            lock = asyncio.Lock()
            _domain_locks[domain] = lock
        return lock
```

### Human delay
```python
def _sample_delay() -> float:
    base = random.gauss(HUMAN_DELAY_MEAN, HUMAN_DELAY_STD)
    base = max(HUMAN_DELAY_MIN, min(HUMAN_DELAY_MAX, base))
    if random.random() < HUMAN_DELAY_LONG_PROB:
        base += random.uniform(_READING_PAUSE_MIN, _READING_PAUSE_MAX)
    return base

async def _human_delay() -> None:
    await asyncio.sleep(_sample_delay())
```

### robots.txt
```python
async def _robots_for(domain: str, scheme: str = "https") -> RobotFileParser:
    async with _robots_cache_guard:
        rp = _robots_cache.get(domain)
        if rp is not None:
            return rp
    rp = RobotFileParser()
    robots_url = f"{scheme}://{domain}/robots.txt"
    session = await _get_session()
    try:
        async with session.get(robots_url, timeout=_ROBOTS_TIMEOUT) as resp:
            if resp.status == 200:
                text = await resp.text(errors="replace")
                rp.parse(text.splitlines())
            elif 500 <= resp.status < 600:
                rp.parse(["User-agent: *", "Disallow: /"])
    except (ClientError, asyncio.TimeoutError):
        rp.parse(["User-agent: *", "Disallow: /"])
    async with _robots_cache_guard:
        _robots_cache.setdefault(domain, rp)
    return rp

def _allowed(rp, url): return rp.can_fetch(USER_AGENT, url)
```

### Backoff
```python
def _backoff_seconds(attempt: int, retry_after: Optional[float]) -> float:
    if retry_after is not None:
        return min(retry_after, RETRY_BACKOFF_CAP)
    raw = (RETRY_BACKOFF_BASE ** attempt) + random.uniform(0, 1)
    return min(raw, RETRY_BACKOFF_CAP)

def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value: return None
    try: return float(value)
    except ValueError:
        from email.utils import parsedate_to_datetime
        try:
            target = parsedate_to_datetime(value)
            return max(0.0, target.timestamp() - time.time())
        except (TypeError, ValueError):
            return None
```

### get() core
```python
async def get(url, *, headers=None, params=None) -> bytes:
    domain = _domain(url)
    if not domain:
        raise FetchPermanentError(url, 0, "malformed URL")
    scheme = urlparse(url).scheme or "https"
    rp = await _robots_for(domain, scheme=scheme)
    if not _allowed(rp, url):
        raise FetchRobotsBlocked(f"robots disallows {url}")

    session = await _get_session()
    lock = await _lock_for(domain)

    async with lock:
        await _human_delay()
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(url, headers=headers, params=params) as resp:
                    body = await resp.read()
                    status = resp.status
                    if 200 <= status < 300:
                        return body
                    if status == 429 or 500 <= status < 600:
                        retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                        sleep_s = _backoff_seconds(attempt, retry_after)
                        log.warning("fetch %s → %d, attempt %d/%d, sleep %.1fs",
                                    url, status, attempt, MAX_RETRIES, sleep_s)
                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(sleep_s); continue
                        raise FetchTimeoutError(f"{status} on {url} after {MAX_RETRIES}")
                    raise FetchPermanentError(url, status, resp.reason or "")
            except (ClientError, asyncio.TimeoutError) as e:
                last_exc = e
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(_backoff_seconds(attempt, None)); continue
                raise FetchTimeoutError(f"network error on {url}: {e}") from e
        raise FetchTimeoutError(f"unreachable: exhausted retries on {url}") from last_exc
```

### get_text + head
```python
async def get_text(url, *, headers=None, params=None) -> str:
    body = await get(url, headers=headers, params=params)
    return body.decode("utf-8", errors="replace")

async def head(url, *, headers=None) -> int:
    domain = _domain(url)
    if not domain:
        raise FetchPermanentError(url, 0, "malformed URL")
    rp = await _robots_for(domain, scheme=urlparse(url).scheme or "https")
    if not _allowed(rp, url):
        raise FetchRobotsBlocked(f"robots disallows {url}")
    session = await _get_session()
    lock = await _lock_for(domain)
    async with lock:
        await _human_delay()
        try:
            async with session.head(url, headers=headers, allow_redirects=True) as resp:
                return resp.status
        except (ClientError, asyncio.TimeoutError) as e:
            raise FetchTimeoutError(f"HEAD failed on {url}: {e}") from e
```

---

## Tests (`tests/test_fetch.py`)

Use `pytest`, `pytest-asyncio`, `aioresponses`. See expanded test list in
the original spec; key cases:
- 200 returns bytes; 404 raises FetchPermanentError; 429+Retry-After retries; 5xx exhausts
- robots disallow/allow; per-domain serialise + cross-domain parallel
- HEAD returns int even on 4xx; reading-pause activation under fixed seed
- close() resets session

---

## Acceptance Criteria

- aiohttp only; no `requests`. get/get_text/head/close as documented.
- One ClientSession per process; one asyncio.Lock per domain; parallel across domains.
- Gaussian + 10% reading pause; robots cached per domain; 5xx-on-robots → deny-all.
- Backoff with `Retry-After` (seconds or HTTP-date).
- No imports from cfp.db / cfp.queue / cfp.models / cfp.parsers / cfp.llm.

---

## Downstream Consumers

| Module | Usage |
|---|---|
| `cfp/parsers/*.py` | `await fetch.get_text(url)` |
| `cfp/llm/tools.py` | `await fetch.head(url)` for liveness (R15) |
| `cfp/pipeline.py` | exception routing → cfp:dead vs re-enqueue |
| `cfp/cli.py` | `await fetch.close()` on shutdown |
