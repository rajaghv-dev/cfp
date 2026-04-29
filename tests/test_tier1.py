"""Unit tests for cfp/llm/tier1.py.

Mocks ``OllamaClient.chat`` so no live model is invoked. Hits the live
``cfp_postgres`` container for the events / tier_runs side-effects.
"""
from __future__ import annotations

import asyncio
import json

import psycopg
import pytest
from psycopg.rows import dict_row

from config import PG_DSN
from cfp import db
from cfp.llm import TierEscalation
from cfp.llm import tier1 as t1


_TRUNCATE_TABLES = [
    "tier_runs", "event_embeddings", "concept_embeddings",
    "event_organisations", "event_people", "person_affiliations",
    "events", "people", "venues", "series", "organisations",
    "scrape_sessions", "sites", "scrape_queue",
]


@pytest.fixture(scope="session", autouse=True)
def _init_schema():
    db.init_db()
    yield


@pytest.fixture()
def db_conn():
    conn = psycopg.connect(PG_DSN, row_factory=dict_row)
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE " + ", ".join(_TRUNCATE_TABLES) + " RESTART IDENTITY CASCADE"
        )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers — mock OllamaClient.chat
# ---------------------------------------------------------------------------


class _ChatScript:
    """Replay a sequence of canned chat() responses."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, messages, *args, **kwargs):
        self.calls.append({"messages": messages, "args": args, "kwargs": kwargs})
        if not self.responses:
            return ""
        return self.responses.pop(0)


@pytest.fixture()
def patch_chat(monkeypatch):
    """Return a setter that swaps OllamaClient.chat for a scripted stub."""

    def _set(responses: list[str]) -> _ChatScript:
        script = _ChatScript(responses)
        monkeypatch.setattr(
            "cfp.llm.tier1.OllamaClient.chat",
            lambda self, messages, *a, **kw: script(messages, *a, **kw),
            raising=True,
        )
        # Avoid hitting Redis from incr_metric in retry path.
        monkeypatch.setattr(
            "cfp.llm.tier1.queue.incr_metric",
            _aiono,
            raising=True,
        )
        monkeypatch.setattr(
            "cfp.llm.tier1.queue.push_tier2",
            _aiono,
            raising=True,
        )
        monkeypatch.setattr(
            "cfp.llm.tier1.queue.push_escalation",
            _aiono,
            raising=True,
        )
        return script

    return _set


async def _aiono(*args, **kwargs):
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tier1_valid_json_inserts_row(db_conn, patch_chat):
    payload = {
        "is_cfp": True,
        "is_workshop": False,
        "categories": ["AI", "ML"],
        "is_virtual": False,
        "confidence": 0.95,
    }
    patch_chat([json.dumps(payload)])

    result = asyncio.run(
        t1.run_tier1(
            "ICML 2026 — International Conference on Machine Learning. "
            "Submissions due 2026-01-30.",
            "https://icml.cc/Conferences/2026",
            scrape_session_id=None,
        )
    )

    assert result.is_cfp is True
    assert result.event_id > 0
    assert result.confidence == pytest.approx(0.95)
    assert result.is_workshop is False

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT event_id, acronym FROM events WHERE event_id = %s",
            (result.event_id,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["event_id"] == result.event_id

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT escalate FROM tier_runs WHERE event_id = %s AND tier = 1",
            (result.event_id,),
        )
        run_row = cur.fetchone()
    assert run_row is not None
    assert run_row["escalate"] is False


def test_tier1_malformed_json_recovered_via_repair(db_conn, patch_chat):
    # Single-quoted keys + trailing commas — repair_json fixes both.
    raw = (
        "{'is_cfp': true, 'is_workshop': false, 'categories': ['AI'], "
        "'is_virtual': false, 'confidence': 0.92,}"
    )
    script = patch_chat([raw])

    result = asyncio.run(
        t1.run_tier1(
            "AAAI 2026: AI conference",
            "https://aaai.org/conference",
            scrape_session_id=None,
        )
    )

    assert result.is_cfp is True
    assert result.event_id > 0
    # Only one chat() call needed — repair was sufficient.
    assert len(script.calls) == 1


def test_tier1_persistent_malformed_escalates(db_conn, patch_chat):
    patch_chat(["not json", "still not json", "really not"])

    with pytest.raises(TierEscalation) as excinfo:
        asyncio.run(
            t1.run_tier1(
                "ICLR 2026 — Learning Representations",
                "https://iclr.cc",
                scrape_session_id=None,
            )
        )
    assert excinfo.value.reason == "json_parse_fail"
    assert excinfo.value.target_tier == 2


def test_tier1_low_confidence_escalates(db_conn, patch_chat):
    payload = {
        "is_cfp": True,
        "is_workshop": False,
        "categories": ["AI"],
        "is_virtual": False,
        "confidence": 0.5,
    }
    patch_chat([json.dumps(payload)])

    with pytest.raises(TierEscalation) as excinfo:
        asyncio.run(
            t1.run_tier1(
                "Some uncertain CFP page text",
                "https://example.org/cfp",
                scrape_session_id=None,
            )
        )
    assert excinfo.value.reason == "low_confidence"
    assert excinfo.value.target_tier == 2

    # Row WAS inserted (acceptance criterion).
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM events")
        n_events = cur.fetchone()["n"]
        cur.execute(
            "SELECT escalate, escalate_reason FROM tier_runs WHERE tier = 1"
        )
        run = cur.fetchone()
    assert n_events == 1
    assert run["escalate"] is True
    assert run["escalate_reason"] == "low_confidence"


def test_tier1_not_cfp_returns_minus_one_no_event(db_conn, patch_chat):
    payload = {
        "is_cfp": False,
        "is_workshop": False,
        "categories": [],
        "is_virtual": False,
        "confidence": 0.99,
    }
    patch_chat([json.dumps(payload)])

    result = asyncio.run(
        t1.run_tier1(
            "Subscribe to our newsletter for AI news!",
            "https://newsletter.example.com",
            scrape_session_id=None,
        )
    )

    assert result.is_cfp is False
    assert result.event_id == -1

    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM events")
        n_events = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM tier_runs WHERE event_id IS NULL")
        n_runs = cur.fetchone()["n"]
    assert n_events == 0
    assert n_runs == 1
