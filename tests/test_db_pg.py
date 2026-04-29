"""Integration tests for cfp/db.py against the live cfp_postgres container.

Requires the cfp_postgres Docker container running on localhost:5432
(see docker-compose.yml). Tests run init_db() once per session, then truncate
between tests.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from config import PG_DSN
from cfp import db
from cfp.models import (
    Category, Event, Organisation, OrgType, Person, PersonRole, Series, Venue,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Per task brief: the 13 core tables expected by init_db().
# (concept_embeddings is a bonus from codegen/05 §step 8 and not asserted here.)
EXPECTED_TABLES = {
    "organisations", "series", "venues", "people", "scrape_sessions",
    "events", "event_people", "person_affiliations", "event_organisations",
    "event_embeddings", "sites", "tier_runs", "scrape_queue",
}

# Tables to truncate between tests (CASCADE handles the FK ordering).
_TRUNCATE_TABLES = [
    "tier_runs", "event_embeddings", "concept_embeddings",
    "event_organisations", "event_people", "person_affiliations",
    "events", "people", "venues", "series", "organisations",
    "scrape_sessions", "sites", "scrape_queue",
]


@pytest.fixture(scope="session", autouse=True)
def _init_schema():
    """Run init_db() once per pytest session."""
    db.init_db()
    yield


@pytest.fixture()
def db_conn():
    """Per-test connection; truncates all tables before yielding."""
    conn = psycopg.connect(PG_DSN, row_factory=dict_row)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE " + ", ".join(_TRUNCATE_TABLES) + " RESTART IDENTITY CASCADE")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def session_id(db_conn):
    return db.begin_session(db_conn, machine="test", git_sha="deadbeef",
                            prompts_md_sha="cafebabe")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_init_db_idempotent():
    db.init_db()
    db.init_db()  # second call must not raise


def test_all_expected_tables_exist(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
        present = {r["table_name"] for r in cur.fetchall()}
    missing = EXPECTED_TABLES - present
    assert not missing, f"missing tables: {missing}"


def test_events_superseded_by_column_exists(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_name = 'events' AND column_name = 'superseded_by'
            """
        )
        row = cur.fetchone()
    assert row is not None, "events.superseded_by column missing"
    assert row["data_type"] == "integer"


# ---------------------------------------------------------------------------
# upsert_event COALESCE behaviour
# ---------------------------------------------------------------------------

def _make_event(event_id: int, **overrides) -> Event:
    base = dict(
        event_id=event_id,
        acronym="TEST",
        name="Test Event",
        categories=[Category.AI],
        is_workshop=False,
        is_virtual=False,
        paper_deadline=date(2026, 6, 1),
        notification=date(2026, 7, 15),
        rank="A",
        description="initial description",
        notes="",
        source="wikicfp",
    )
    base.update(overrides)
    return Event(**base)


def test_upsert_event_coalesce_preserves_notification(db_conn, session_id):
    first = _make_event(900001, scrape_session_id=session_id)
    db.upsert_event(db_conn, first)

    # Second pass with notification=None must NOT erase the value.
    second = _make_event(
        900001,
        notification=None,
        rank=None,                # also COALESCE-protected
        description=None,         # also COALESCE-protected
        scrape_session_id=session_id,
    )
    db.upsert_event(db_conn, second)

    row = db.get_event_by_id(db_conn, 900001)
    assert row is not None
    assert row["notification"] == date(2026, 7, 15)
    assert row["rank"] == "A"
    assert row["description"] == "initial description"


def test_upsert_event_overwrites_paper_deadline(db_conn, session_id):
    first = _make_event(900002, scrape_session_id=session_id)
    db.upsert_event(db_conn, first)

    # Deadline extension is the one case where we DO overwrite.
    second = _make_event(900002, paper_deadline=date(2026, 7, 10),
                         scrape_session_id=session_id)
    db.upsert_event(db_conn, second)

    row = db.get_event_by_id(db_conn, 900002)
    assert row["paper_deadline"] == date(2026, 7, 10)


# ---------------------------------------------------------------------------
# Tier-1 placeholder + tier_runs audit
# ---------------------------------------------------------------------------

def test_insert_minimal_event_returns_serial_id(db_conn, session_id):
    eid = db.insert_minimal_event(
        db_conn,
        acronym="ABC", name="Anon", categories=[Category.AI],
        is_workshop=False, is_virtual=True,
        origin_url="http://example.com/cfp",
        source="wikicfp", scrape_session_id=session_id,
    )
    assert eid > 0
    row = db.get_event_by_id(db_conn, eid)
    assert row["acronym"] == "ABC"
    assert row["categories"] == ["AI"]
    assert row["is_virtual"] is True


def test_insert_tier_run_with_null_event_id(db_conn, session_id):
    # Tier 1 discards (is_cfp=False) — event_id must be allowed to be NULL.
    db.insert_tier_run(
        db_conn,
        event_id=None, tier=1, model="qwen3:4b-q4_K_M",
        confidence=0.92, output_json={"is_cfp": False},
        escalate=False, escalate_reason=None,
        elapsed_ms=42, scrape_session_id=session_id,
    )
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM tier_runs WHERE event_id IS NULL")
        assert cur.fetchone()["n"] == 1


def test_insert_tier_run_with_event_id(db_conn, session_id):
    eid = db.insert_minimal_event(
        db_conn, acronym="XYZ", name="X", categories=[Category.AI],
        is_workshop=False, is_virtual=False, origin_url=None,
        source="wikicfp", scrape_session_id=session_id,
    )
    db.insert_tier_run(
        db_conn, event_id=eid, tier=1, model="qwen3:4b-q4_K_M",
        confidence=0.9, output_json={"ok": True},
        escalate=False, escalate_reason=None,
        elapsed_ms=10, scrape_session_id=session_id,
    )
    with db_conn.cursor() as cur:
        cur.execute("SELECT * FROM tier_runs WHERE event_id = %s", (eid,))
        row = cur.fetchone()
    assert row["confidence"] == pytest.approx(0.9)
    assert row["output_json"] == {"ok": True}


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def test_begin_and_end_session_round_trip(db_conn):
    sid = db.begin_session(db_conn, machine="gpu_mid",
                           git_sha="abc1234", prompts_md_sha="deadbeef")
    assert isinstance(sid, str) and len(sid) > 10
    with db_conn.cursor() as cur:
        cur.execute("SELECT * FROM scrape_sessions WHERE session_id = %s", (sid,))
        row = cur.fetchone()
    assert row is not None
    assert row["machine"] == "gpu_mid"
    assert row["git_sha"] == "abc1234"
    assert row["finished_at"] is None

    db.end_session(db_conn, sid)
    with db_conn.cursor() as cur:
        cur.execute("SELECT finished_at FROM scrape_sessions WHERE session_id = %s", (sid,))
        finished = cur.fetchone()["finished_at"]
    assert finished is not None


# ---------------------------------------------------------------------------
# seed_from_json
# ---------------------------------------------------------------------------

def test_seed_from_json_small_fixture(db_conn, session_id, tmp_path):
    fixture = [
        {
            "acronym": "FIXC 2026",
            "name": "Fixture Conf 2026",
            "category": "AI, ML",
            "keywords": ["foo", "bar"],
            "when": "May 1, 2026 - May 3, 2026",
            "where": "Berlin, Germany",
            "deadline": "Mar 1, 2026",
            "url": "http://www.wikicfp.com/cfp/servlet/event.showcfp?eventid=987654&copyownerid=1",
        },
        {
            "acronym": "FIXJ 2026",
            "name": "Journal-shaped",
            "category": "AI",
            "keywords": ["x"],
            "when": "N/A",
            "where": "N/A",
            "deadline": "Apr 5, 2026",
            "url": "http://www.wikicfp.com/cfp/servlet/event.showcfp?eventid=987655&copyownerid=2",
        },
    ]
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture))

    n = db.seed_from_json(db_conn, path, session_id)
    assert n == 2

    with db_conn.cursor() as cur:
        cur.execute("SELECT event_id, acronym, paper_deadline, categories, edition_year "
                    "FROM events ORDER BY event_id")
        rows = cur.fetchall()
    ids = {r["event_id"] for r in rows}
    assert ids == {987654, 987655}
    by_id = {r["event_id"]: r for r in rows}
    assert by_id[987654]["paper_deadline"] == date(2026, 3, 1)
    assert by_id[987654]["edition_year"] == 2026
    assert "AI" in by_id[987654]["categories"]
    assert "ML" in by_id[987654]["categories"]


# ---------------------------------------------------------------------------
# collapse_event
# ---------------------------------------------------------------------------

def test_collapse_event_reroutes_event_people_and_marks_superseded(db_conn, session_id):
    # Create two events: src (loser) and dst (winner)
    src = db.insert_minimal_event(
        db_conn, acronym="DUP", name="Duplicate Event",
        categories=[Category.AI], is_workshop=False, is_virtual=False,
        origin_url="http://a.example.com", source="wikicfp",
        scrape_session_id=session_id,
    )
    dst = db.insert_minimal_event(
        db_conn, acronym="DUP", name="Canonical Event",
        categories=[Category.AI], is_workshop=False, is_virtual=False,
        origin_url="http://b.example.com", source="wikicfp",
        scrape_session_id=session_id,
    )

    # Person attached to src, role=general_chair
    pid = db.upsert_person(db_conn, Person(person_id=0, full_name="Alice"))
    db.link_event_person(db_conn, src, pid, PersonRole.GENERAL_CHAIR.value)

    # Org attached to src
    oid = db.upsert_org(db_conn, Organisation(org_id=0, name="ACM",
                                              type=OrgType.PUBLISHER))
    db.link_event_org(db_conn, src, oid, "sponsor")

    # Tier run attached to src
    db.insert_tier_run(
        db_conn, event_id=src, tier=2, model="qwen3:14b",
        confidence=0.9, output_json={"v": 1},
        escalate=False, escalate_reason=None, elapsed_ms=100,
        scrape_session_id=session_id,
    )

    db.collapse_event(db_conn, src=src, dst=dst)

    with db_conn.cursor() as cur:
        cur.execute("SELECT event_id, role FROM event_people WHERE person_id = %s", (pid,))
        ep = cur.fetchall()
        cur.execute("SELECT event_id, role FROM event_organisations WHERE org_id = %s", (oid,))
        eo = cur.fetchall()
        cur.execute("SELECT event_id FROM tier_runs WHERE model = 'qwen3:14b'")
        tr = cur.fetchall()
        cur.execute("SELECT superseded_by FROM events WHERE event_id = %s", (src,))
        src_row = cur.fetchone()

    assert ep == [{"event_id": dst, "role": "general_chair"}]
    assert eo == [{"event_id": dst, "role": "sponsor"}]
    assert tr == [{"event_id": dst}]
    assert src_row["superseded_by"] == dst


def test_collapse_event_pk_collision_safe(db_conn, session_id):
    """When dst already has the same (person, role), src's row is dropped."""
    src = db.insert_minimal_event(
        db_conn, acronym="DUP", name="src",
        categories=[Category.AI], is_workshop=False, is_virtual=False,
        origin_url=None, source="wikicfp", scrape_session_id=session_id,
    )
    dst = db.insert_minimal_event(
        db_conn, acronym="DUP", name="dst",
        categories=[Category.AI], is_workshop=False, is_virtual=False,
        origin_url=None, source="wikicfp", scrape_session_id=session_id,
    )
    pid = db.upsert_person(db_conn, Person(person_id=0, full_name="Bob"))
    db.link_event_person(db_conn, src, pid, "pc_chair")
    db.link_event_person(db_conn, dst, pid, "pc_chair")

    db.collapse_event(db_conn, src=src, dst=dst)

    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM event_people WHERE person_id = %s", (pid,))
        assert cur.fetchone()["n"] == 1
