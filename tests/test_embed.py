"""Unit tests for cfp/embed.py — httpx mocked via respx.

Zero network: every test routes /api/embed through respx. Each test runs as
a single coroutine inside a fresh event loop so the shared httpx.AsyncClient
is born and closed in the same loop.
"""
from __future__ import annotations

import asyncio
import json
import math

import httpx
import pytest
import respx

from config import EMBED_DIM, OLLAMA_HOST
from cfp import embed


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def _isolate_module():
    embed.clear_cache()
    # Force a brand-new client per test — the previous loop is closed.
    embed._client = None
    yield
    if embed._client is not None:
        # Close on a fresh loop; httpx tolerates aclose on a finished loop
        # because we discard the reference immediately after.
        try:
            _run(embed.aclose())
        except RuntimeError:
            embed._client = None


def _vec(seed: int) -> list[float]:
    return [(seed + i) * 0.001 for i in range(EMBED_DIM)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@respx.mock
def test_embed_one_returns_768_floats():
    route = respx.post(f"{OLLAMA_HOST}/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [_vec(1)]})
    )

    async def scenario():
        out = await embed.embed_one("hello")
        await embed.aclose()
        return out

    out = _run(scenario())
    assert isinstance(out, list)
    assert len(out) == EMBED_DIM
    assert all(isinstance(x, (int, float)) for x in out)
    assert route.call_count == 1


@respx.mock
def test_embed_many_batches_100_into_two_calls():
    counter = {"n": 0}

    def _handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        n_in = len(json.loads(request.read())["input"])
        return httpx.Response(
            200, json={"embeddings": [_vec(i) for i in range(n_in)]}
        )

    respx.post(f"{OLLAMA_HOST}/api/embed").mock(side_effect=_handler)
    texts = [f"text-{i}" for i in range(100)]

    async def scenario():
        out = await embed.embed_many(texts)
        await embed.aclose()
        return out

    out = _run(scenario())
    assert len(out) == 100
    assert counter["n"] == math.ceil(100 / embed._BATCH_SIZE) == 2


@respx.mock
def test_cache_hit_avoids_second_http_call():
    route = respx.post(f"{OLLAMA_HOST}/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [_vec(7)]})
    )

    async def scenario():
        await embed.embed_one("same-text")
        await embed.embed_one("same-text")
        await embed.aclose()

    _run(scenario())
    assert route.call_count == 1
    stats = embed.cache_stats()
    assert stats["hits"] >= 1
    assert stats["size"] == 1


@respx.mock
def test_embed_many_partial_cache_only_sends_uncached():
    counter = {"n": 0, "sizes": []}

    def _handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        sent = json.loads(request.read())["input"]
        counter["sizes"].append(len(sent))
        return httpx.Response(
            200, json={"embeddings": [_vec(42) for _ in sent]}
        )

    respx.post(f"{OLLAMA_HOST}/api/embed").mock(side_effect=_handler)

    async def scenario():
        # Pre-warm two of three texts.
        await embed.embed_one("a")
        await embed.embed_one("b")
        pre = counter["n"]
        out = await embed.embed_many(["a", "b", "c"])
        await embed.aclose()
        return pre, out

    pre_calls, out = _run(scenario())
    assert pre_calls == 2
    assert len(out) == 3
    # Only "c" went over the wire as a third (size-1) batch.
    assert counter["n"] == pre_calls + 1
    assert counter["sizes"][-1] == 1


@respx.mock
def test_wrong_dim_raises_runtimeerror():
    bad = [0.1] * (EMBED_DIM - 1)
    respx.post(f"{OLLAMA_HOST}/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [bad]})
    )

    async def scenario():
        try:
            await embed.embed_one("x")
        finally:
            await embed.aclose()

    with pytest.raises(RuntimeError, match="expected dim"):
        _run(scenario())


@respx.mock
def test_lru_eviction_at_small_cache_max(monkeypatch):
    monkeypatch.setattr(embed, "_CACHE_MAX", 3)

    def _handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.read())["input"]
        return httpx.Response(
            200, json={"embeddings": [_vec(1) for _ in sent]}
        )

    respx.post(f"{OLLAMA_HOST}/api/embed").mock(side_effect=_handler)

    async def scenario():
        for t in ("a", "b", "c", "d"):
            await embed.embed_one(t)
        await embed.aclose()

    _run(scenario())
    stats = embed.cache_stats()
    assert stats["size"] == 3  # "a" was evicted
