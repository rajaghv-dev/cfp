"""pgvector access layer for the CFP pipeline.

The only module that touches ``event_embeddings`` / ``concept_embeddings``
(codegen/08). Pure DB — zero httpx / ollama / cfp.embed imports.

Cosine similarity is derived from pgvector's ``<=>`` operator
(cosine distance): ``cosine = 1 - distance``.

IVFFlat index: ``lists = max(100, floor(sqrt(N)))`` — see
``rebuild_ivfflat``.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pgvector.psycopg import register_vector

from config import (
    DEDUP_AUTO_MERGE,
    DEDUP_COSINE,
    DEDUP_TOP_K,
    EMBED_DIM,
    PG_DSN,
)


# ---------------------------------------------------------------------------
# Connection pool (lazy)
# ---------------------------------------------------------------------------

_pool: ConnectionPool | None = None


def _configure(conn: psycopg.Connection) -> None:
    # register_vector adapts list[float] <-> pgvector.vector once per pooled
    # connection so callers don't need ::vector casts at every query site.
    register_vector(conn)
    conn.row_factory = dict_row


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=PG_DSN,
            min_size=1,
            max_size=8,
            configure=_configure,
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Neighbour:
    id: int | str
    name: str
    cosine: float
    payload: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Table whitelist (defensive against SQL injection — keys are never templated)
# ---------------------------------------------------------------------------

_TABLES: dict[str, dict[str, str]] = {
    "events": {
        "embed_table": "event_embeddings",
        "id_col":      "event_id",
        "join_table":  "events",
        "join_id":     "event_id",
        "label_col":   "acronym",
        "ivf_index":   "event_embeddings_vec_ivf",
    },
    "concepts": {
        "embed_table": "concept_embeddings",
        "id_col":      "concept_name",
        "join_table":  "concepts",
        "join_id":     "name",
        "label_col":   "name",
        "ivf_index":   "concept_embeddings_vec_ivf",
    },
}


def _cfg(table: str) -> dict[str, str]:
    if table not in _TABLES:
        raise ValueError(f"unknown table key: {table!r}")
    return _TABLES[table]


def _check_dim(vec: Iterable[float]) -> list[float]:
    v = list(vec)
    if len(v) != EMBED_DIM:
        raise ValueError(f"vec must be {EMBED_DIM}-d, got {len(v)}")
    return v


# ---------------------------------------------------------------------------
# Read paths
# ---------------------------------------------------------------------------


def find_neighbours(
    vec: Iterable[float],
    *,
    table: str,
    top_k: int = 5,
    min_cosine: float = 0.0,
) -> list[Neighbour]:
    """Top-k ANN neighbours by cosine distance (``<=>``).

    Returns rows in monotonic descending cosine order.
    """
    v = _check_dim(vec)
    cfg = _cfg(table)
    sql = f"""
        SELECT emb.{cfg['id_col']}                       AS id,
               COALESCE(j.{cfg['label_col']}, '')        AS name,
               1 - (emb.vec <=> %(vec)s::vector)                 AS cosine
        FROM {cfg['embed_table']} emb
        LEFT JOIN {cfg['join_table']} j
               ON j.{cfg['join_id']} = emb.{cfg['id_col']}
        WHERE 1 - (emb.vec <=> %(vec)s::vector) >= %(min_cosine)s
        ORDER BY emb.vec <=> %(vec)s::vector
        LIMIT %(top_k)s
    """
    pool = _get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"vec": v, "min_cosine": min_cosine, "top_k": top_k})
        rows = cur.fetchall()
    return [
        Neighbour(id=r["id"], name=r["name"], cosine=float(r["cosine"]))
        for r in rows
    ]


def is_duplicate(vec: Iterable[float]) -> Optional[int]:
    """Return the matching ``event_id`` if any neighbour has cosine
    >= ``DEDUP_AUTO_MERGE``, else ``None``. One SQL round-trip."""
    v = _check_dim(vec)
    cfg = _cfg("events")
    sql = f"""
        SELECT emb.{cfg['id_col']} AS id,
               1 - (emb.vec <=> %(vec)s::vector) AS cosine
        FROM {cfg['embed_table']} emb
        WHERE 1 - (emb.vec <=> %(vec)s::vector) >= %(min_cosine)s
        ORDER BY emb.vec <=> %(vec)s::vector
        LIMIT 1
    """
    pool = _get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"vec": v, "min_cosine": DEDUP_AUTO_MERGE})
        row = cur.fetchone()
    if row is None:
        return None
    return int(row["id"])


def query_with_filter(
    vec: Iterable[float],
    *,
    where: str,
    params: dict[str, Any],
    top_k: int = 10,
    table: str = "events",
    select_extra: Optional[list[str]] = None,
) -> list[Neighbour]:
    """Combined SQL ``WHERE`` filter + vector ORDER BY.

    ``where`` is interpolated verbatim (caller's responsibility); ``params``
    binds named placeholders. The reserved key ``vec`` is rejected — the
    embedding parameter is supplied internally.
    """
    if "vec" in params:
        raise ValueError("'vec' is a reserved param key")
    v = _check_dim(vec)
    cfg = _cfg(table)
    extra_cols = ""
    if select_extra:
        extra_cols = ", " + ", ".join(f"j.{c} AS {c}" for c in select_extra)
    sql = f"""
        SELECT emb.{cfg['id_col']}                AS id,
               COALESCE(j.{cfg['label_col']}, '') AS name,
               1 - (emb.vec <=> %(vec)s::vector)          AS cosine
               {extra_cols}
        FROM {cfg['embed_table']} emb
        JOIN {cfg['join_table']} j
          ON j.{cfg['join_id']} = emb.{cfg['id_col']}
        WHERE {where}
        ORDER BY emb.vec <=> %(vec)s::vector
        LIMIT %(top_k)s
    """
    bind = {**params, "vec": v, "top_k": top_k}
    pool = _get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, bind)
        rows = cur.fetchall()
    out: list[Neighbour] = []
    for r in rows:
        payload = {c: r[c] for c in (select_extra or [])}
        out.append(
            Neighbour(
                id=r["id"],
                name=r["name"],
                cosine=float(r["cosine"]),
                payload=payload,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Write paths
# ---------------------------------------------------------------------------


def upsert_event_embedding(
    event_id: int,
    vec: Iterable[float],
    text_hash: str,
) -> None:
    v = _check_dim(vec)
    pool = _get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO event_embeddings (event_id, vec, text_hash)
            VALUES (%s, %s, %s)
            ON CONFLICT (event_id) DO UPDATE SET
                vec = EXCLUDED.vec,
                text_hash = EXCLUDED.text_hash
            """,
            (event_id, v, text_hash),
        )
        conn.commit()


def upsert_concept_embedding(name: str, vec: Iterable[float]) -> None:
    v = _check_dim(vec)
    pool = _get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO concept_embeddings (concept_name, vec)
            VALUES (%s, %s)
            ON CONFLICT (concept_name) DO UPDATE SET
                vec = EXCLUDED.vec
            """,
            (name, v),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Operational hooks
# ---------------------------------------------------------------------------


def enqueue_for_dedup_review(event_id: int, vec: Iterable[float]) -> int:
    """Push grey-zone (cosine in [DEDUP_COSINE, DEDUP_AUTO_MERGE)) candidates
    to ``cfp:dedup_pending`` for human / Tier-3 review.

    v1: import redis lazily so this module stays free of redis at import time.
    Returns the new list length (LPUSH return value), or 0 on failure.
    """
    v = _check_dim(vec)
    try:
        import redis  # local import keeps top-level imports DB/stdlib-only
        from config import REDIS_URL
        client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        payload = json.dumps({"event_id": int(event_id), "vec": v},
                             separators=(",", ":"))
        return int(client.lpush("cfp:dedup_pending", payload))
    except Exception:
        return 0


def rebuild_ivfflat(table: str) -> None:
    """DROP + CREATE the IVFFlat index on (vec) using cosine ops.

    ``lists = max(100, floor(sqrt(N)))`` — pgvector's rule of thumb. The min
    of 100 keeps tiny tables from collapsing into a single list.
    """
    cfg = _cfg(table)
    # CREATE INDEX takes a bare identifier; the index inherits the table's
    # schema. DROP INDEX accepts schema-qualified names so we qualify it
    # using the embed_table's schema (if any).
    bare_idx = cfg["ivf_index"].split(".")[-1]
    if "." in cfg["embed_table"]:
        drop_target = f"{cfg['embed_table'].split('.')[0]}.{bare_idx}"
    else:
        drop_target = bare_idx
    pool = _get_pool()
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {cfg['embed_table']}")
        row = cur.fetchone()
        n = int(row["n"]) if row else 0
        lists = max(100, int(math.floor(math.sqrt(max(n, 1)))))
        cur.execute(f"DROP INDEX IF EXISTS {drop_target}")
        cur.execute(
            f"CREATE INDEX {bare_idx} "
            f"ON {cfg['embed_table']} USING ivfflat (vec vector_cosine_ops) "
            f"WITH (lists = {lists})"
        )
        conn.commit()
