# Codegen 08 — cfp/embed.py + cfp/vectors.py

## Files to Create
- `cfp/embed.py` — embedding generation via Ollama (HTTP only, no DB)
- `cfp/vectors.py` — pgvector queries (DB only, no Ollama)
- `tests/test_embed.py`, `tests/test_vectors.py`
- `scripts/test_pgvector_extended.sh`

## Rule
Strict split: `embed.py` is the only module that talks to `OLLAMA_HOST` for
embeddings; `vectors.py` is the only module that touches `event_embeddings`
or `concept_embeddings`. No cross-imports between the two.

---

## 1. cfp/embed.py

### Imports
```python
from __future__ import annotations
import asyncio, hashlib
from collections import OrderedDict
from typing import Iterable
import httpx
from config import OLLAMA_HOST, EMBED_DIM
```

### Constants
```python
_EMBED_MODEL = "nomic-embed-text"
_EMBED_ENDPOINT = "/api/embed"
_BATCH_SIZE = 64                  # arch.md S14: 32-64 per call
_HTTP_TIMEOUT = 120.0
_CACHE_MAX = 10_000
```

### State
```python
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()
_cache: "OrderedDict[str, list[float]]" = OrderedDict()
_cache_hits = 0
_cache_misses = 0
```

### Public API
```python
async def embed_one(text: str) -> list[float]:
    """Cached by sha1(text). Returns 768-d list[float]."""

async def embed_many(texts: Iterable[str]) -> list[list[float]]:
    """Batched: ceil(N/64) HTTP calls, preserves input order, hits cache where possible."""

def cache_stats() -> dict[str, int]: ...
def clear_cache() -> None: ...
async def aclose() -> None: ...
```

### Key implementation
```python
async def embed_one(text):
    cached = _cache_get(text)
    if cached is not None: return cached
    client = await _get_client()
    resp = await client.post(_EMBED_ENDPOINT,
                             json={"model": _EMBED_MODEL, "input": [text]})
    resp.raise_for_status()
    vec = resp.json()["embeddings"][0]
    if len(vec) != EMBED_DIM:
        raise RuntimeError(f"expected dim {EMBED_DIM}, got {len(vec)}")
    _cache_put(text, vec)
    return vec

async def embed_many(texts):
    items = list(texts)
    out = [None] * len(items)
    pending = []
    for i, t in enumerate(items):
        c = _cache_get(t)
        if c is not None: out[i] = c
        else: pending.append((i, t))
    if not pending: return out
    client = await _get_client()
    for chunk_start in range(0, len(pending), _BATCH_SIZE):
        chunk = pending[chunk_start:chunk_start + _BATCH_SIZE]
        resp = await client.post(_EMBED_ENDPOINT,
            json={"model": _EMBED_MODEL, "input": [t for _, t in chunk]})
        resp.raise_for_status()
        vecs = resp.json()["embeddings"]
        for (orig_idx, t), v in zip(chunk, vecs):
            if len(v) != EMBED_DIM:
                raise RuntimeError(f"expected dim {EMBED_DIM}, got {len(v)}")
            _cache_put(t, v)
            out[orig_idx] = v
    return out
```

---

## 2. cfp/vectors.py

### Imports
```python
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Any, Literal, Optional
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector
from config import (PG_DSN, EMBED_DIM, DEDUP_COSINE, DEDUP_AUTO_MERGE, DEDUP_TOP_K)
```

### Connection pool (lazy)
```python
_pool: ConnectionPool | None = None

def _configure(conn):
    register_vector(conn)
    conn.row_factory = dict_row

def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(conninfo=PG_DSN, min_size=1, max_size=8,
                               configure=_configure, open=True)
    return _pool

def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
```

### Neighbour dataclass
```python
@dataclass(slots=True)
class Neighbour:
    id: int | str
    name: str
    cosine: float
    payload: dict[str, Any] = field(default_factory=dict)
```

### Table whitelist
```python
_TABLES = {
    "events":   {"embed_table": "event_embeddings", "id_col": "event_id",
                 "join_table": "events", "join_id": "event_id",
                 "label_col": "acronym", "ivf_index": "event_embeddings_vec_ivf"},
    "concepts": {"embed_table": "concept_embeddings", "id_col": "concept_name",
                 "join_table": "concepts", "join_id": "name",
                 "label_col": "name", "ivf_index": "concept_embeddings_vec_ivf"},
}
```

### Public API
```python
def find_neighbours(vec, *, table, top_k=5, min_cosine=0.0) -> list[Neighbour]: ...
def is_duplicate(vec) -> Optional[int]: ...   # cosine >= DEDUP_AUTO_MERGE → event_id
def enqueue_for_dedup_review(event_id, vec) -> int: ...
def rebuild_ivfflat(table) -> None: ...    # lists = max(100, floor(sqrt(N)))
def query_with_filter(vec, *, where, params, top_k=10, table="events", select_extra=None) -> list[Neighbour]: ...
def upsert_event_embedding(event_id, vec, text_hash) -> None: ...
def upsert_concept_embedding(name, vec) -> None: ...
```

### find_neighbours implementation
```python
def find_neighbours(vec, *, table, top_k=5, min_cosine=0.0):
    if len(vec) != EMBED_DIM:
        raise ValueError(f"vec must be {EMBED_DIM}-d, got {len(vec)}")
    cfg = _TABLES[table]
    sql = f"""
        SELECT emb.{cfg['id_col']} AS id,
               COALESCE(j.{cfg['label_col']}, '') AS name,
               1 - (emb.vec <=> %(vec)s) AS cosine
        FROM {cfg['embed_table']} emb
        LEFT JOIN {cfg['join_table']} j ON j.{cfg['join_id']} = emb.{cfg['id_col']}
        WHERE 1 - (emb.vec <=> %(vec)s) >= %(min_cosine)s
        ORDER BY emb.vec <=> %(vec)s LIMIT %(top_k)s
    """
    pool = _get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"vec": vec, "min_cosine": min_cosine, "top_k": top_k})
        return [Neighbour(id=r["id"], name=r["name"], cosine=float(r["cosine"]))
                for r in cur.fetchall()]
```

---

## Tests

### tests/test_embed.py (mock httpx via respx)
- embed_one returns 768-d list[float]
- embed_many of 100 inputs uses 2 HTTP calls (batch=64)
- cache hit avoids second HTTP call
- partial cache hit: only un-cached subset sent
- wrong dim raises RuntimeError
- LRU eviction at _CACHE_MAX

### tests/test_vectors.py (integration; requires cfp_postgres)
- find_neighbours top-5 monotonic descending cosine
- is_duplicate returns event_id at cosine 0.99; None at 0.95
- rebuild_ivfflat: lists = max(100, floor(sqrt(N)))
- query_with_filter: WHERE country='US' returns only US events
- query_with_filter rejects reserved 'vec' param key
- dim mismatch raises ValueError

### scripts/test_pgvector_extended.sh
Shell-side smoke tests covering: ANN top-5, is_duplicate threshold,
IVFFlat rebuild lists clamping, combined SQL+vector filter, EXPLAIN
confirms index scan, dim-mismatch rejection. Schema: temp `cfp_pgvtest`.

---

## Dependency additions (requirements.txt)
```
psycopg[binary]>=3.1
psycopg-pool>=3.2
pgvector>=0.3
httpx>=0.27
respx>=0.21       # tests only
pytest-asyncio>=0.23
```

---

## Acceptance Criteria

- `cfp/embed.py` has zero psycopg/cfp.db/cfp.vectors imports.
- `cfp/vectors.py` has zero httpx/ollama/cfp.embed imports.
- `register_vector(conn)` runs once per pooled connection.
- `embed_many(N)` uses ceil(N/64) HTTP requests; cache hits skip wire.
- `is_duplicate(v)` is a single SQL query (top-1 ANN with min_cosine filter).
- `rebuild_ivfflat` clamps lists ≥ 100; uses `floor(sqrt(N))` above that.
- `query_with_filter` rejects `params` containing reserved `vec` key.

---

## Downstream Consumers

| Module | Usage |
|---|---|
| `cfp/pipeline.py` | embed_many for batch; is_duplicate per write; upsert_event_embedding |
| `cfp/dedup.py` | find_neighbours for sweep; is_duplicate for hot path |
| `cfp/cli.py` | rebuild_ivfflat for `cfp run-pipeline --rebuild-index` |
| `cfp/ontology/*` (v2) | embed_one / find_neighbours(table="concepts") |

Only module that imports `pgvector` package or talks to Ollama for embeddings.
