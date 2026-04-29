"""Integration tests for cfp/vectors.py against the live cfp_postgres container.

Uses an isolated schema (``test_vectors_iso``) and monkeypatches
``cfp.vectors._TABLES`` to point at the test tables. The real
``event_embeddings`` / ``concept_embeddings`` tables are never touched.
"""
from __future__ import annotations

import math

import psycopg
import pytest
from pgvector.psycopg import register_vector

from config import EMBED_DIM, PG_DSN
from cfp import vectors


SCHEMA = "test_vectors_iso"


def _vec(seed: int) -> list[float]:
    """Deterministic, normalised-ish vector. Cosine similarity with seed 0
    is monotonic in |seed - other| for small magnitudes."""
    base = [0.0] * EMBED_DIM
    base[0] = 1.0
    base[1] = seed * 0.01
    return base


def _near(seed_target: float) -> list[float]:
    """Vector with a tunable cosine vs. ``_vec(0)``."""
    base = [0.0] * EMBED_DIM
    base[0] = 1.0
    base[1] = seed_target
    return base


# ---------------------------------------------------------------------------
# Schema fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _schema():
    conn = psycopg.connect(PG_DSN, autocommit=True)
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        cur.execute(f"CREATE SCHEMA {SCHEMA}")
        cur.execute(
            f"""
            CREATE TABLE {SCHEMA}.events (
                event_id SERIAL PRIMARY KEY,
                acronym  VARCHAR,
                country  VARCHAR
            )
            """
        )
        cur.execute(
            f"""
            CREATE TABLE {SCHEMA}.event_embeddings (
                event_id  INTEGER PRIMARY KEY
                          REFERENCES {SCHEMA}.events(event_id),
                vec       vector({EMBED_DIM}),
                text_hash VARCHAR
            )
            """
        )
    conn.close()
    yield
    conn = psycopg.connect(PG_DSN, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    conn.close()


@pytest.fixture(autouse=True)
def _redirect_tables(monkeypatch):
    """Point cfp.vectors at our isolated schema."""
    # ivf_index is unqualified — Postgres places it in the table's schema.
    monkeypatch.setitem(
        vectors._TABLES,
        "events",
        {
            "embed_table": f"{SCHEMA}.event_embeddings",
            "id_col":      "event_id",
            "join_table":  f"{SCHEMA}.events",
            "join_id":     "event_id",
            "label_col":   "acronym",
            "ivf_index":   "event_embeddings_vec_ivf",
        },
    )
    yield
    vectors.close_pool()


@pytest.fixture()
def truncate_each():
    """TRUNCATE between tests so vectors written by one test don't leak."""
    conn = psycopg.connect(PG_DSN, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(
            f"TRUNCATE {SCHEMA}.event_embeddings, {SCHEMA}.events "
            f"RESTART IDENTITY CASCADE"
        )
    conn.close()
    yield


def _insert_event(country: str = "US", acronym: str = "TST") -> int:
    """Insert a parent events row, return its event_id."""
    conn = psycopg.connect(PG_DSN, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {SCHEMA}.events (acronym, country) "
                f"VALUES (%s, %s) RETURNING event_id",
                (acronym, country),
            )
            return int(cur.fetchone()[0])
    finally:
        conn.close()


def _insert_embedding(event_id: int, vec: list[float]) -> None:
    conn = psycopg.connect(PG_DSN, autocommit=True)
    register_vector(conn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {SCHEMA}.event_embeddings "
                f"(event_id, vec, text_hash) VALUES (%s, %s, %s)",
                (event_id, vec, f"h{event_id}"),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_find_neighbours_top5_monotonic_descending(truncate_each):
    # 10 events whose vectors drift away from _vec(0) at increasing rate.
    for i in range(10):
        eid = _insert_event(country="US", acronym=f"E{i:02d}")
        _insert_embedding(eid, _vec(i))

    out = vectors.find_neighbours(_vec(0), table="events", top_k=5)
    assert len(out) == 5
    cosines = [n.cosine for n in out]
    assert cosines == sorted(cosines, reverse=True)
    # First result is the exact match (cosine == 1.0 within float tolerance).
    assert out[0].cosine == pytest.approx(1.0, abs=1e-6)


def test_is_duplicate_threshold(truncate_each):
    # Insert one near-duplicate (cosine ~ 0.9999).
    eid = _insert_event()
    _insert_embedding(eid, _vec(0))

    near = list(_vec(0))
    near[1] = 0.005  # cosine ~ 0.9999875 vs. _vec(0)
    assert vectors.is_duplicate(near) == eid

    # And one far enough to fall under DEDUP_AUTO_MERGE.
    far = [0.0] * EMBED_DIM
    far[0] = 0.4
    far[1] = 1.0  # cosine vs. _vec(0) ~ 0.371
    assert vectors.is_duplicate(far) is None


def test_rebuild_ivfflat_lists_clamps_to_min_100(truncate_each):
    # 10 rows → floor(sqrt(10)) == 3, clamped to 100.
    for i in range(10):
        eid = _insert_event(acronym=f"S{i}")
        _insert_embedding(eid, _vec(i))
    vectors.rebuild_ivfflat("events")

    conn = psycopg.connect(PG_DSN, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT indexdef FROM pg_indexes
                WHERE schemaname = %s AND indexname = %s
                """,
                (SCHEMA, "event_embeddings_vec_ivf"),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    indexdef = row[0].lower()
    assert "ivfflat" in indexdef
    # Pg normalises WITH options to single-quoted strings: lists='100'.
    assert "lists='100'" in indexdef or "lists = 100" in indexdef \
        or "lists=100" in indexdef


def test_query_with_filter_country_us(truncate_each):
    us_ids = []
    for i in range(3):
        eid = _insert_event(country="US", acronym=f"US{i}")
        _insert_embedding(eid, _vec(i))
        us_ids.append(eid)
    for i in range(3):
        eid = _insert_event(country="DE", acronym=f"DE{i}")
        _insert_embedding(eid, _vec(i + 3))

    out = vectors.query_with_filter(
        _vec(0),
        where="j.country = %(country)s",
        params={"country": "US"},
        top_k=10,
        table="events",
    )
    assert len(out) == 3
    assert {n.id for n in out} == set(us_ids)


def test_query_with_filter_rejects_reserved_vec_param(truncate_each):
    with pytest.raises(ValueError, match="reserved"):
        vectors.query_with_filter(
            _vec(0),
            where="1 = 1",
            params={"vec": [0.0] * EMBED_DIM},
        )


def test_dim_mismatch_raises_valueerror(truncate_each):
    bad = [0.0] * (EMBED_DIM - 1)
    with pytest.raises(ValueError, match=f"{EMBED_DIM}-d"):
        vectors.find_neighbours(bad, table="events")
    with pytest.raises(ValueError, match=f"{EMBED_DIM}-d"):
        vectors.is_duplicate(bad)
