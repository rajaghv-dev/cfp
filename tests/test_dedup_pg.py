"""Integration tests for cfp/dedup.py against the live cfp_postgres container.

Marker: ``pg``. Skipped automatically if the container is unreachable or the
``redis`` package is missing.
"""
from __future__ import annotations

from datetime import date

import psycopg
import pytest
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from config import EMBED_DIM, PG_DSN, REDIS_URL
from cfp import db, dedup
from cfp.dedup import (
    DEDUP_QUEUE_KEY,
    Candidate,
    SweepReport,
    acronym_blocking,
    find_candidates,
    merge_events,
    precheck_duplicate,
    sweep,
)
from cfp.models import Category, Person, PersonRole


pytestmark = pytest.mark.pg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Same table list used by tests/test_db_pg.py.
_TRUNCATE_TABLES = [
    "tier_runs", "event_embeddings", "concept_embeddings",
    "event_organisations", "event_people", "person_affiliations",
    "events", "people", "venues", "series", "organisations",
    "scrape_sessions", "sites", "scrape_queue",
]


def _vec(seed: int) -> list[float]:
    """Deterministic vector along axis 0 with a small ``seed``-driven tilt
    on axis 1. Matches the helper in tests/test_vectors.py."""
    base = [0.0] * EMBED_DIM
    base[0] = 1.0
    base[1] = seed * 0.01
    return base


def _vec_for_cosine(target: float) -> list[float]:
    """Build a vector whose cosine similarity with ``_vec(0)`` is ``target``.

    cos(theta) = a/sqrt(a^2 + b^2). Set b = sqrt((1-target^2)) / target * a,
    then ``a=1.0`` keeps numbers reasonable.
    """
    if target >= 1.0:
        v = _vec(0)
        v[1] = 1e-7
        return v
    import math
    a = 1.0
    b = math.sqrt(1.0 - target * target) / target
    v = [0.0] * EMBED_DIM
    v[0] = a
    v[1] = b
    return v


def _insert_embedding(conn, event_id: int, vec: list[float]) -> None:
    """Insert a vector via a separately-registered connection.

    Uses raw_conn-style execution because the shared ``db_conn`` fixture
    isn't pgvector-registered. We open a fresh autocommit connection.
    """
    raw = psycopg.connect(PG_DSN, autocommit=True)
    register_vector(raw)
    try:
        with raw.cursor() as cur:
            cur.execute(
                "INSERT INTO event_embeddings (event_id, vec, text_hash) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (event_id) DO UPDATE SET vec = EXCLUDED.vec",
                (event_id, vec, f"h{event_id}"),
            )
    finally:
        raw.close()


def _get_redis():
    import redis
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _init_schema():
    db.init_db()
    yield


@pytest.fixture()
def clean_db():
    """Per-test connection; truncates all tables and clears the dedup queue."""
    conn = psycopg.connect(PG_DSN, row_factory=dict_row)
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE " + ", ".join(_TRUNCATE_TABLES) + " RESTART IDENTITY CASCADE"
        )
    conn.commit()
    try:
        r = _get_redis()
        r.delete(DEDUP_QUEUE_KEY)
    except Exception:
        pass
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def session_id(clean_db):
    return db.begin_session(clean_db, machine="test", git_sha="dead",
                            prompts_md_sha="beef")


def _insert_event(conn, *, acronym="EVT", name="Event", year=None,
                  source="wikicfp", scrape_session_id=None,
                  description=None, rank=None) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (acronym, name, edition_year, source,
                                scrape_session_id, description, rank,
                                categories, is_workshop, is_virtual)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING event_id
            """,
            (acronym, name, year, source, scrape_session_id,
             description, rank, ["AI"], False, False),
        )
        eid = int(cur.fetchone()["event_id"])
    conn.commit()
    return eid


# ---------------------------------------------------------------------------
# precheck_duplicate
# ---------------------------------------------------------------------------


def test_precheck_returns_existing_id_when_cosine_above_auto_merge(
    clean_db, session_id
):
    """Cosine 0.99 → returns the existing event_id and DOES NOT push to queue."""
    eid = _insert_event(clean_db, acronym="A", scrape_session_id=session_id)
    _insert_embedding(clean_db, eid, _vec(0))

    # Tiny perturbation on axis 1 → cosine ~0.99995.
    near = _vec(0)
    near[1] = 0.01

    r = _get_redis()
    r.delete(DEDUP_QUEUE_KEY)

    got = precheck_duplicate(near)
    assert got == eid
    assert r.llen(DEDUP_QUEUE_KEY) == 0


def test_precheck_grey_zone_returns_none_and_queues(clean_db, session_id):
    """Cosine in [0.92, 0.97) → returns None, pushes one queue entry."""
    eid = _insert_event(clean_db, acronym="B", scrape_session_id=session_id)
    _insert_embedding(clean_db, eid, _vec(0))

    grey = _vec_for_cosine(0.95)

    r = _get_redis()
    r.delete(DEDUP_QUEUE_KEY)

    got = precheck_duplicate(grey)
    assert got is None
    assert r.llen(DEDUP_QUEUE_KEY) == 1

    import json
    payload = json.loads(r.lindex(DEDUP_QUEUE_KEY, 0))
    assert payload["existing_event_id"] == eid
    assert 0.92 <= float(payload["cosine"]) < 0.97
    assert payload["reason"] == "ann_topk_pending"


def test_precheck_below_threshold_returns_none_silently(
    clean_db, session_id
):
    """Cosine < 0.92 → returns None without touching the queue."""
    eid = _insert_event(clean_db, acronym="C", scrape_session_id=session_id)
    _insert_embedding(clean_db, eid, _vec(0))

    far = _vec_for_cosine(0.85)

    r = _get_redis()
    r.delete(DEDUP_QUEUE_KEY)

    got = precheck_duplicate(far)
    assert got is None
    assert r.llen(DEDUP_QUEUE_KEY) == 0


def test_precheck_skips_superseded_rows(clean_db, session_id):
    """A loser (superseded_by IS NOT NULL) must not be returned by precheck."""
    winner = _insert_event(clean_db, acronym="W", scrape_session_id=session_id)
    loser = _insert_event(clean_db, acronym="L", scrape_session_id=session_id)
    # Winner cosine vs probe ~ 0.37 -- well below DEDUP_COSINE.
    _insert_embedding(clean_db, winner, _vec_for_cosine(0.30))
    _insert_embedding(clean_db, loser, _vec(0))     # otherwise the closest

    # Mark loser superseded -- but keep the embedding around so we can prove
    # the WHERE clause is what excludes it (not row deletion).
    with clean_db.cursor() as cur:
        cur.execute(
            "UPDATE events SET superseded_by = %s WHERE event_id = %s",
            (winner, loser),
        )
    clean_db.commit()

    r = _get_redis()
    r.delete(DEDUP_QUEUE_KEY)

    near = _vec(0)
    got = precheck_duplicate(near)
    # Must ignore the superseded loser entirely; winner is far away → None.
    assert got != loser
    # The remaining row's cosine vs probe is far below DEDUP_COSINE → None +
    # no queue activity.
    assert got is None
    assert r.llen(DEDUP_QUEUE_KEY) == 0


# ---------------------------------------------------------------------------
# merge_events
# ---------------------------------------------------------------------------


def test_merge_events_lossless_field_copy(clean_db, session_id):
    """Non-null winner fields stay; null winner fields fill from loser."""
    winner = _insert_event(
        clean_db, acronym="W", name="Winner", year=2026,
        rank="A", scrape_session_id=session_id,
        description=None,  # null on winner -> should pick up loser's
    )
    loser = _insert_event(
        clean_db, acronym="L", name="Loser", year=2026,
        rank="B",  # winner has A → must NOT overwrite
        description="loser-desc",
        scrape_session_id=session_id,
    )

    merge_events(winner, loser, reason="unit-test")

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT acronym, name, rank, description, superseded_by, notes "
            "FROM events WHERE event_id = %s",
            (winner,),
        )
        w = cur.fetchone()
        cur.execute(
            "SELECT superseded_by FROM events WHERE event_id = %s",
            (loser,),
        )
        l = cur.fetchone()

    # Winner's non-null fields are preserved.
    assert w["acronym"] == "W"
    assert w["rank"] == "A"
    # Winner's null fields are filled from loser.
    assert w["description"] == "loser-desc"
    assert w["superseded_by"] is None
    assert l["superseded_by"] == winner


def test_merge_events_reroutes_event_people(clean_db, session_id):
    winner = _insert_event(clean_db, acronym="W", scrape_session_id=session_id)
    loser = _insert_event(clean_db, acronym="L", scrape_session_id=session_id)

    pid = db.upsert_person(clean_db, Person(person_id=0, full_name="Alice"))
    db.link_event_person(clean_db, loser, pid, PersonRole.GENERAL_CHAIR.value)

    merge_events(winner, loser, reason="reroute")

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT event_id, role FROM event_people WHERE person_id = %s",
            (pid,),
        )
        rows = cur.fetchall()
    assert rows == [{"event_id": winner, "role": "general_chair"}]


def test_merge_events_pk_collision_safe(clean_db, session_id):
    """Same person+role on both sides → loser's row dropped without error."""
    winner = _insert_event(clean_db, acronym="W", scrape_session_id=session_id)
    loser = _insert_event(clean_db, acronym="L", scrape_session_id=session_id)
    pid = db.upsert_person(clean_db, Person(person_id=0, full_name="Bob"))
    db.link_event_person(clean_db, winner, pid, "pc_chair")
    db.link_event_person(clean_db, loser, pid, "pc_chair")

    merge_events(winner, loser, reason="collision")

    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS n FROM event_people WHERE person_id = %s",
            (pid,),
        )
        assert cur.fetchone()["n"] == 1


def test_merge_events_rejects_already_superseded_loser(clean_db, session_id):
    a = _insert_event(clean_db, acronym="A", scrape_session_id=session_id)
    b = _insert_event(clean_db, acronym="B", scrape_session_id=session_id)
    c = _insert_event(clean_db, acronym="C", scrape_session_id=session_id)

    merge_events(a, b, reason="first")

    with pytest.raises(ValueError, match="already superseded"):
        merge_events(c, b, reason="second")


# ---------------------------------------------------------------------------
# acronym_blocking
# ---------------------------------------------------------------------------


def test_acronym_blocking_three_events_yield_three_pairs(
    clean_db, session_id
):
    """Three events with normalised acronym 'icra' + same year → C(3,2)=3 pairs."""
    a = _insert_event(clean_db, acronym="ICRA-2025", year=2025,
                      scrape_session_id=session_id)
    b = _insert_event(clean_db, acronym="ICRA 2025", year=2025,
                      scrape_session_id=session_id)
    c = _insert_event(clean_db, acronym="ICRA 25", year=2025,
                      scrape_session_id=session_id)
    # AAAI must be excluded -- different normalised acronym.
    _insert_event(clean_db, acronym="AAAI 2025", year=2025,
                  scrape_session_id=session_id)
    # Same acronym, different year -- excluded.
    _insert_event(clean_db, acronym="ICRA 2024", year=2024,
                  scrape_session_id=session_id)

    pairs = acronym_blocking()
    expected = sorted([
        (min(a, b), max(a, b)),
        (min(a, c), max(a, c)),
        (min(b, c), max(b, c)),
    ])
    assert sorted(pairs) == expected


# ---------------------------------------------------------------------------
# sweep -- end-to-end smoke
# ---------------------------------------------------------------------------


def test_sweep_finds_known_duplicate(clean_db, session_id):
    """Two events, identical embeddings → sweep auto-merges them."""
    a = _insert_event(clean_db, acronym="DUP", year=2026,
                      scrape_session_id=session_id)
    b = _insert_event(clean_db, acronym="DUP", year=2026,
                      scrape_session_id=session_id)
    # Add a non-dup so the sweep has at least three live rows to scan.
    c = _insert_event(clean_db, acronym="OTHER", year=2026,
                      scrape_session_id=session_id)

    _insert_embedding(clean_db, a, _vec(0))
    _insert_embedding(clean_db, b, _vec(0))
    # cos(c, a) ~ 0.30 -- far below DEDUP_COSINE so c is not a candidate.
    _insert_embedding(clean_db, c, _vec_for_cosine(0.30))

    report = sweep()
    assert isinstance(report, SweepReport)
    assert report.merges_applied >= 1

    # Exactly one of {a, b} should now be superseded; c untouched.
    with clean_db.cursor() as cur:
        cur.execute(
            "SELECT event_id, superseded_by FROM events WHERE event_id = ANY(%s)",
            ([a, b, c],),
        )
        rows = {r["event_id"]: r["superseded_by"] for r in cur.fetchall()}
    superseded = [eid for eid, sup in rows.items() if sup is not None]
    assert len(superseded) == 1
    assert rows[c] is None
