"""Ollama embedding client for the CFP pipeline.

The only module that talks to ``OLLAMA_HOST`` for embeddings (codegen/08).
Pure HTTP — zero psycopg / cfp.db / cfp.vectors imports.

Cache: sha1(text) keyed LRU, cap = ``_CACHE_MAX``. Hits skip the wire.
Batching: ``embed_many`` sends ceil(N/_BATCH_SIZE) HTTP calls and preserves
input order even when some entries are cache hits.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from typing import Iterable

import httpx

from config import EMBED_DIM, OLLAMA_HOST


_EMBED_MODEL = "nomic-embed-text"
_EMBED_ENDPOINT = "/api/embed"
_BATCH_SIZE = 64
_HTTP_TIMEOUT = 120.0
_CACHE_MAX = 10_000


_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()
_cache: "OrderedDict[str, list[float]]" = OrderedDict()
_cache_hits = 0
_cache_misses = 0


def _key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _cache_get(text: str) -> list[float] | None:
    global _cache_hits, _cache_misses
    k = _key(text)
    if k in _cache:
        # LRU: bump on access so eviction hits the truly cold entries.
        _cache.move_to_end(k)
        _cache_hits += 1
        return _cache[k]
    _cache_misses += 1
    return None


def _cache_put(text: str, vec: list[float]) -> None:
    k = _key(text)
    if k in _cache:
        _cache.move_to_end(k)
        _cache[k] = vec
        return
    _cache[k] = vec
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(
                    base_url=OLLAMA_HOST,
                    timeout=_HTTP_TIMEOUT,
                )
    return _client


async def embed_one(text: str) -> list[float]:
    """Return the 768-d embedding for ``text``. Cached by sha1(text)."""
    cached = _cache_get(text)
    if cached is not None:
        return cached
    client = await _get_client()
    resp = await client.post(
        _EMBED_ENDPOINT,
        json={"model": _EMBED_MODEL, "input": [text]},
    )
    resp.raise_for_status()
    vec = resp.json()["embeddings"][0]
    if len(vec) != EMBED_DIM:
        raise RuntimeError(f"expected dim {EMBED_DIM}, got {len(vec)}")
    _cache_put(text, vec)
    return vec


async def embed_many(texts: Iterable[str]) -> list[list[float]]:
    """Batched embedding. ceil(len(texts)/_BATCH_SIZE) HTTP calls; cache hits
    short-circuit. Output order matches input order."""
    items = list(texts)
    out: list[list[float] | None] = [None] * len(items)
    pending: list[tuple[int, str]] = []
    for i, t in enumerate(items):
        c = _cache_get(t)
        if c is not None:
            out[i] = c
        else:
            pending.append((i, t))
    if not pending:
        return [v for v in out if v is not None]  # type: ignore[return-value]

    client = await _get_client()
    for chunk_start in range(0, len(pending), _BATCH_SIZE):
        chunk = pending[chunk_start:chunk_start + _BATCH_SIZE]
        resp = await client.post(
            _EMBED_ENDPOINT,
            json={"model": _EMBED_MODEL,
                  "input": [t for _, t in chunk]},
        )
        resp.raise_for_status()
        vecs = resp.json()["embeddings"]
        if len(vecs) != len(chunk):
            raise RuntimeError(
                f"expected {len(chunk)} embeddings, got {len(vecs)}"
            )
        for (orig_idx, t), v in zip(chunk, vecs):
            if len(v) != EMBED_DIM:
                raise RuntimeError(f"expected dim {EMBED_DIM}, got {len(v)}")
            _cache_put(t, v)
            out[orig_idx] = v
    return out  # type: ignore[return-value]


def cache_stats() -> dict[str, int]:
    return {"size": len(_cache), "hits": _cache_hits, "misses": _cache_misses}


def clear_cache() -> None:
    global _cache_hits, _cache_misses
    _cache.clear()
    _cache_hits = 0
    _cache_misses = 0


async def aclose() -> None:
    """Close the shared httpx client. Safe to call multiple times."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
