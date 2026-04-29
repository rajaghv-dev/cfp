"""Tests for cfp/analytics.py.

Integration-tested against the live cfp_postgres container."""
from __future__ import annotations

import pytest
import psycopg
from datetime import date

from config import PG_DSN
from cfp import analytics
from cfp import db


@pytest.fixture(autouse=True)
def _clean_events():
    """Truncate events table before each test (and the whole chain via CASCADE)."""
    with psycopg.connect(PG_DSN, autocommit=True) as conn, conn.cursor() as cur:
        try:
            cur.execute(
                "TRUNCATE events, event_embeddings, event_people, "
                "event_organisations, tier_runs RESTART IDENTITY CASCADE"
            )
        except psycopg.errors.UndefinedTable:
            pass
    yield


def _insert_event(eid: int, acronym: str, name: str,
                  paper_deadline: date | None = None,
                  categories: list[str] | None = None,
                  superseded_by: int | None = None) -> None:
    with psycopg.connect(PG_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO events (event_id, acronym, name, paper_deadline,
                                categories, superseded_by, source)
            VALUES (%s, %s, %s, %s, %s, %s, 'wikicfp')
        """, (eid, acronym, name, paper_deadline, categories or [], superseded_by))


def test_load_events_from_pg_empty():
    db.init_db()
    events = analytics.load_events_from_pg()
    assert events == []


def test_load_events_from_pg_returns_dicts():
    db.init_db()
    _insert_event(100, "ICML", "Intl Conf on Machine Learning",
                  paper_deadline=date(2025, 2, 1),
                  categories=["ML"])
    events = analytics.load_events_from_pg()
    assert len(events) == 1
    e = events[0]
    assert e["acronym"] == "ICML"
    assert e["name"] == "Intl Conf on Machine Learning"
    assert e["deadline"] == "2025-02-01"  # ISO string per legacy format
    assert "ML" in e["categories"]


def test_load_events_from_pg_excludes_superseded():
    db.init_db()
    _insert_event(200, "ICRA", "ICRA 2025")
    _insert_event(201, "ICRA", "ICRA 2025 dup", superseded_by=200)
    events = analytics.load_events_from_pg()
    assert len(events) == 1
    assert events[0]["acronym"] == "ICRA"


def test_load_events_orders_by_deadline_then_acronym():
    db.init_db()
    _insert_event(300, "B", "Beta", paper_deadline=date(2025, 6, 1))
    _insert_event(301, "A", "Alpha", paper_deadline=date(2025, 3, 1))
    _insert_event(302, "C", "Charlie")  # no deadline → NULLS LAST
    events = analytics.load_events_from_pg()
    acronyms = [e["acronym"] for e in events]
    # A (earlier deadline) before B (later); C (no deadline) last.
    assert acronyms == ["A", "B", "C"]


def test_row_to_legacy_dict_handles_missing_fields():
    """Defensive: rows with NULL fields shouldn't crash the converter."""
    minimal = {"event_id": 1, "acronym": "X", "name": "X",
               "paper_deadline": None, "abstract_deadline": None,
               "notification": None, "categories": None,
               "where_raw": None, "when_raw": None,
               "official_url": None, "origin_url": None,
               "description": None, "raw_tags": None,
               "is_workshop": False, "country": None, "rank": None}
    out = analytics._row_to_legacy_dict(minimal)
    assert out["acronym"] == "X"
    assert out["deadline"] == ""
    assert out["categories"] == []
