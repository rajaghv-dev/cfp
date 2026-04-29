"""Async HTTP fetch layer for the CFP scraper.

aiohttp-based, per-domain Gaussian rate limit + occasional reading pause,
robots.txt cache, exponential backoff with Retry-After. No DB / Redis / LLM
dependencies — returns raw bytes; parsers handle decoding.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import aiohttp
from aiohttp import ClientError

from config import (
    USER_AGENT,
    HUMAN_DELAY_MEAN, HUMAN_DELAY_STD, HUMAN_DELAY_MIN, HUMAN_DELAY_MAX,
    HUMAN_DELAY_LONG_PROB,
    MAX_RETRIES, RETRY_BACKOFF_BASE, RETRY_BACKOFF_CAP,
)

log = logging.getLogger(__name__)


_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
_READING_PAUSE_MIN = 15.0
_READING_PAUSE_MAX = 45.0
_ROBOTS_TIMEOUT = aiohttp.ClientTimeout(total=10)


class FetchError(Exception):
    """Base for fetch-layer exceptions."""


class FetchPermanentError(FetchError):
    """4xx other than 429 — caller must not retry."""

    def __init__(self, url: str, status: int, message: str = ""):
        self.url = url
        self.status = status
        super().__init__(f"{status} on {url}: {message}")


class FetchTimeoutError(FetchError):
    """Exhausted MAX_RETRIES on 429/5xx/network errors."""


class FetchRobotsBlocked(FetchError):
    """robots.txt disallows this URL for our User-Agent."""


_session: Optional[aiohttp.ClientSession] = None
_session_lock: Optional[asyncio.Lock] = None
_domain_locks: dict[str, asyncio.Lock] = {}
_domain_locks_guard: Optional[asyncio.Lock] = None
_robots_cache: dict[str, RobotFileParser] = {}
_robots_cache_guard: Optional[asyncio.Lock] = None


def _ensure_guards() -> None:
    # asyncio.Lock() at module import time would bind to whatever loop is
    # current then (often none) — create lazily inside an async context.
    global _session_lock, _domain_locks_guard, _robots_cache_guard
    if _session_lock is None:
        _session_lock = asyncio.Lock()
    if _domain_locks_guard is None:
        _domain_locks_guard = asyncio.Lock()
    if _robots_cache_guard is None:
        _robots_cache_guard = asyncio.Lock()


async def _get_session() -> aiohttp.ClientSession:
    global _session
    _ensure_guards()
    if _session is None or _session.closed:
        assert _session_lock is not None
        async with _session_lock:
            if _session is None or _session.closed:
                connector = aiohttp.TCPConnector(
                    limit=20, limit_per_host=4, ttl_dns_cache=300
                )
                _session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=_REQUEST_TIMEOUT,
                    headers={"User-Agent": USER_AGENT},
                )
    return _session


async def close() -> None:
    """Close the shared ClientSession. Idempotent."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def _domain(url: str) -> str:
    p = urlparse(url)
    return (p.hostname or "").lower()


async def _lock_for(domain: str) -> asyncio.Lock:
    _ensure_guards()
    assert _domain_locks_guard is not None
    async with _domain_locks_guard:
        lock = _domain_locks.get(domain)
        if lock is None:
            lock = asyncio.Lock()
            _domain_locks[domain] = lock
        return lock


def _sample_delay() -> float:
    base = random.gauss(HUMAN_DELAY_MEAN, HUMAN_DELAY_STD)
    base = max(HUMAN_DELAY_MIN, min(HUMAN_DELAY_MAX, base))
    if random.random() < HUMAN_DELAY_LONG_PROB:
        base += random.uniform(_READING_PAUSE_MIN, _READING_PAUSE_MAX)
    return base


async def _human_delay() -> None:
    await asyncio.sleep(_sample_delay())


async def _robots_for(domain: str, scheme: str = "https") -> RobotFileParser:
    _ensure_guards()
    assert _robots_cache_guard is not None
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
                # 5xx on robots → treat as deny-all (conservative; arch.md §S13).
                rp.parse(["User-agent: *", "Disallow: /"])
            else:
                # 4xx (incl 404) → no robots file → allow all per RFC 9309.
                rp.parse(["User-agent: *", "Allow: /"])
    except (ClientError, asyncio.TimeoutError):
        rp.parse(["User-agent: *", "Disallow: /"])

    async with _robots_cache_guard:
        _robots_cache.setdefault(domain, rp)
    return rp


def _allowed(rp: RobotFileParser, url: str) -> bool:
    return rp.can_fetch(USER_AGENT, url)


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        from email.utils import parsedate_to_datetime
        try:
            target = parsedate_to_datetime(value)
            if target is None:
                return None
            return max(0.0, target.timestamp() - time.time())
        except (TypeError, ValueError):
            return None


def _backoff_seconds(attempt: int, retry_after: Optional[float]) -> float:
    if retry_after is not None:
        return min(retry_after, RETRY_BACKOFF_CAP)
    raw = (RETRY_BACKOFF_BASE ** attempt) + random.uniform(0, 1)
    return min(raw, RETRY_BACKOFF_CAP)


async def get(
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
) -> bytes:
    """Fetch URL; return raw bytes on 2xx.

    Raises:
        FetchRobotsBlocked: robots.txt forbids the URL.
        FetchPermanentError: 4xx (other than 429) — do not retry.
        FetchTimeoutError: exhausted retries on 429/5xx/network.
    """
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
        last_exc: Optional[BaseException] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with session.get(url, headers=headers, params=params) as resp:
                    body = await resp.read()
                    status = resp.status
                    if 200 <= status < 300:
                        return body
                    if status == 429 or 500 <= status < 600:
                        retry_after = _parse_retry_after(
                            resp.headers.get("Retry-After")
                        )
                        sleep_s = _backoff_seconds(attempt, retry_after)
                        log.warning(
                            "fetch %s -> %d, attempt %d/%d, sleep %.1fs",
                            url, status, attempt, MAX_RETRIES, sleep_s,
                        )
                        if attempt < MAX_RETRIES:
                            await asyncio.sleep(sleep_s)
                            continue
                        raise FetchTimeoutError(
                            f"{status} on {url} after {MAX_RETRIES} attempts"
                        )
                    raise FetchPermanentError(url, status, resp.reason or "")
            except (ClientError, asyncio.TimeoutError) as e:
                last_exc = e
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(_backoff_seconds(attempt, None))
                    continue
                raise FetchTimeoutError(
                    f"network error on {url}: {e}"
                ) from e
        raise FetchTimeoutError(
            f"unreachable: exhausted retries on {url}"
        ) from last_exc


async def get_text(
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
) -> str:
    """get() + utf-8 decode with errors='replace'."""
    body = await get(url, headers=headers, params=params)
    return body.decode("utf-8", errors="replace")


async def head(url: str, *, headers: Optional[dict] = None) -> int:
    """HEAD request — returns status as int (no 4xx raise).

    Subject to robots + per-domain rate limit. Network failure raises
    FetchTimeoutError; robots block raises FetchRobotsBlocked.
    """
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
        try:
            async with session.head(
                url, headers=headers, allow_redirects=True
            ) as resp:
                return resp.status
        except (ClientError, asyncio.TimeoutError) as e:
            raise FetchTimeoutError(f"HEAD failed on {url}: {e}") from e
