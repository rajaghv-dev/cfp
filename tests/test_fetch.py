"""Tests for cfp/fetch.py.

Uses pytest-asyncio + aioresponses. Module-level state is reset between
tests by the autouse `_reset_fetch_state` fixture, and the human delay is
neutralised by `_no_delay` so tests run in real time.
"""
from __future__ import annotations

import asyncio
import random
import time
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio  # noqa: F401  (ensures plugin loaded)
from aioresponses import aioresponses

from cfp import fetch
from cfp.fetch import (
    FetchPermanentError,
    FetchRobotsBlocked,
    FetchTimeoutError,
)


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_fetch_state():
    """Reset module-level state between tests."""
    fetch._session = None
    fetch._session_lock = None
    fetch._domain_locks = {}
    fetch._domain_locks_guard = None
    fetch._robots_cache = {}
    fetch._robots_cache_guard = None
    yield
    # Best-effort: tests that opened a session should call close(); if they
    # didn't, drop the reference so the next test rebuilds.
    fetch._session = None
    fetch._session_lock = None
    fetch._domain_locks = {}
    fetch._domain_locks_guard = None
    fetch._robots_cache = {}
    fetch._robots_cache_guard = None


@pytest.fixture
def _no_delay():
    """Skip the Gaussian human delay in tests that don't measure timing."""
    with patch.object(fetch, "_human_delay", new=_anull):
        yield


async def _anull():
    return None


def _allow_all_robots(m: aioresponses, domain: str, scheme: str = "https"):
    m.get(
        f"{scheme}://{domain}/robots.txt",
        status=200,
        body="User-agent: *\nAllow: /\n",
        repeat=True,
    )


def _disallow_robots(m: aioresponses, domain: str, path: str, scheme: str = "https"):
    m.get(
        f"{scheme}://{domain}/robots.txt",
        status=200,
        body=f"User-agent: *\nDisallow: {path}\n",
        repeat=True,
    )


# ---- 200 / 404 / 429 / 5xx -------------------------------------------------


async def test_get_200_returns_bytes(_no_delay):
    url = "https://example.com/page"
    with aioresponses() as m:
        _allow_all_robots(m, "example.com")
        m.get(url, status=200, body=b"hello")
        body = await fetch.get(url)
    assert body == b"hello"
    await fetch.close()


async def test_get_404_raises_permanent(_no_delay):
    url = "https://example.com/missing"
    with aioresponses() as m:
        _allow_all_robots(m, "example.com")
        m.get(url, status=404, reason="Not Found")
        with pytest.raises(FetchPermanentError) as ei:
            await fetch.get(url)
    assert ei.value.status == 404
    await fetch.close()


async def test_429_with_retry_after_then_200(_no_delay):
    url = "https://example.com/throttled"
    with aioresponses() as m:
        _allow_all_robots(m, "example.com")
        m.get(url, status=429, headers={"Retry-After": "0"})
        m.get(url, status=200, body=b"ok")
        body = await fetch.get(url)
    assert body == b"ok"
    await fetch.close()


async def test_5xx_exhausts_retries(_no_delay):
    url = "https://example.com/broken"
    with patch.object(fetch, "_backoff_seconds", return_value=0.0):
        with aioresponses() as m:
            _allow_all_robots(m, "example.com")
            for _ in range(10):  # >= MAX_RETRIES
                m.get(url, status=503)
            with pytest.raises(FetchTimeoutError):
                await fetch.get(url)
    await fetch.close()


async def test_retry_after_http_date(_no_delay):
    """Retry-After accepts an HTTP-date as well as seconds."""
    # Date 1s in future — _parse_retry_after should yield ~1.0.
    future = datetime.now(timezone.utc) + timedelta(seconds=1)
    http_date = format_datetime(future, usegmt=True)
    parsed = fetch._parse_retry_after(http_date)
    assert parsed is not None
    assert 0.0 <= parsed <= 5.0


# ---- robots.txt ------------------------------------------------------------


async def test_robots_disallow_blocks(_no_delay):
    url = "https://example.com/private/secret"
    with aioresponses() as m:
        _disallow_robots(m, "example.com", "/private")
        with pytest.raises(FetchRobotsBlocked):
            await fetch.get(url)
    await fetch.close()


async def test_robots_allow_returns_bytes(_no_delay):
    url = "https://example.com/public/page"
    with aioresponses() as m:
        _disallow_robots(m, "example.com", "/private")
        m.get(url, status=200, body=b"open")
        body = await fetch.get(url)
    assert body == b"open"
    await fetch.close()


async def test_robots_5xx_deny_all(_no_delay):
    url = "https://example.com/anything"
    with aioresponses() as m:
        m.get("https://example.com/robots.txt", status=503, repeat=True)
        with pytest.raises(FetchRobotsBlocked):
            await fetch.get(url)
    await fetch.close()


# ---- per-domain serialise / cross-domain parallelise -----------------------


async def test_per_domain_requests_serialise():
    """Two concurrent same-domain requests must serialise via the lock."""
    url1 = "https://example.com/a"
    url2 = "https://example.com/b"

    async def slow_delay():
        await asyncio.sleep(0.2)

    with patch.object(fetch, "_human_delay", new=slow_delay):
        with aioresponses() as m:
            _allow_all_robots(m, "example.com")
            m.get(url1, status=200, body=b"A")
            m.get(url2, status=200, body=b"B")

            t0 = time.monotonic()
            results = await asyncio.gather(fetch.get(url1), fetch.get(url2))
            elapsed = time.monotonic() - t0

    assert results == [b"A", b"B"]
    # Serialised: ~2 * 0.2s. Parallel would be ~0.2s. Allow generous slack.
    assert elapsed >= 0.35, f"expected serialisation, elapsed={elapsed:.3f}s"
    await fetch.close()


async def test_cross_domain_requests_parallelise():
    """Different domains use independent locks → parallel."""
    url1 = "https://example.com/a"
    url2 = "https://other.com/b"

    async def slow_delay():
        await asyncio.sleep(0.3)

    with patch.object(fetch, "_human_delay", new=slow_delay):
        with aioresponses() as m:
            _allow_all_robots(m, "example.com")
            _allow_all_robots(m, "other.com")
            m.get(url1, status=200, body=b"A")
            m.get(url2, status=200, body=b"B")

            t0 = time.monotonic()
            results = await asyncio.gather(fetch.get(url1), fetch.get(url2))
            elapsed = time.monotonic() - t0

    assert results == [b"A", b"B"]
    # Parallel: ~0.3s. Serial would be ~0.6s.
    assert elapsed < 0.55, f"expected parallel, elapsed={elapsed:.3f}s"
    await fetch.close()


# ---- HEAD ------------------------------------------------------------------


async def test_head_returns_status_int_on_4xx(_no_delay):
    url = "https://example.com/maybe"
    with aioresponses() as m:
        _allow_all_robots(m, "example.com")
        m.head(url, status=404)
        status = await fetch.head(url)
    assert status == 404
    await fetch.close()


async def test_head_returns_200(_no_delay):
    url = "https://example.com/ok"
    with aioresponses() as m:
        _allow_all_robots(m, "example.com")
        m.head(url, status=200)
        status = await fetch.head(url)
    assert status == 200
    await fetch.close()


# ---- get_text --------------------------------------------------------------


async def test_get_text_decodes_utf8(_no_delay):
    url = "https://example.com/txt"
    with aioresponses() as m:
        _allow_all_robots(m, "example.com")
        m.get(url, status=200, body="héllo".encode("utf-8"))
        s = await fetch.get_text(url)
    assert s == "héllo"
    await fetch.close()


# ---- close() ---------------------------------------------------------------


async def test_close_resets_session(_no_delay):
    url = "https://example.com/x"
    with aioresponses() as m:
        _allow_all_robots(m, "example.com")
        m.get(url, status=200, body=b"y")
        await fetch.get(url)
    assert fetch._session is not None
    await fetch.close()
    assert fetch._session is None
    # Idempotent: second close is a no-op.
    await fetch.close()
    assert fetch._session is None


# ---- reading-pause statistics ---------------------------------------------


def test_reading_pause_activates_about_10pct():
    """Under a fixed seed, ~10% of samples include the long reading pause."""
    random.seed(20260429)
    samples = [fetch._sample_delay() for _ in range(1000)]
    # The reading pause adds at least 15s, so any sample > HUMAN_DELAY_MAX
    # (15.0) by a clear margin must have included the pause. Use 15.5s to
    # avoid float boundary noise.
    long_count = sum(1 for s in samples if s > 15.5)
    # 10% target with 1000 samples; 95% CI ~= [80, 120]. Allow [60, 150].
    assert 60 <= long_count <= 150, (
        f"expected ~100 long pauses out of 1000, got {long_count}"
    )
