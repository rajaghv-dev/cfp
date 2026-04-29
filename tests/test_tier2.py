"""Unit tests for cfp/llm/tier2.py.

Five-round prompt chain mocked by replaying canned JSON via a side-effect
list patched onto ``OllamaClient.chat``. Live PostgreSQL is used to verify
COALESCE upsert + tier_runs side-effects; embeddings + vector queries are
also mocked to keep tests independent of nomic-embed-text.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from config import PG_DSN
from cfp import db
from cfp.llm import TierEscalation
from cfp.llm import tier2 as t2


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
# Helpers
# ---------------------------------------------------------------------------


class _ChatScript:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.captured_models: list[str] = []

    def __call__(self, client, messages, *args, **kwargs):
        self.calls.append({"messages": messages, "args": args, "kwargs": kwargs})
        self.captured_models.append(client.model)
        if not self.responses:
            return ""
        return self.responses.pop(0)


async def _aiono(*args, **kwargs):
    return None


@pytest.fixture()
def patch_t2(monkeypatch):
    """Inject scripted chat() + stub queue / vectors / embed."""

    def _set(
        responses: list[str],
        *,
        available_models: list[str] | None = None,
        is_duplicate_returns: int | None = None,
        embedding_vec: list[float] | None = None,
    ) -> _ChatScript:
        script = _ChatScript(responses)
        monkeypatch.setattr(
            "cfp.llm.tier2.OllamaClient.chat",
            lambda self, messages, *a, **kw: script(self, messages, *a, **kw),
            raising=True,
        )
        # Tier 1 also patches its own retry path — tier2 calls _invoke_with_retry
        # which lives in tier1, so its dependent ``queue.incr_metric`` import is
        # already in tier1's module namespace.
        monkeypatch.setattr("cfp.llm.tier1.queue.incr_metric", _aiono)
        monkeypatch.setattr("cfp.llm.tier2.queue.push_escalation", _aiono)
        monkeypatch.setattr("cfp.llm.tier2.queue.push_tier2", _aiono)

        models = available_models if available_models is not None else [
            "qwen3:14b-q4_K_M", "qwen3:4b-q4_K_M", "nomic-embed-text",
        ]
        monkeypatch.setattr(
            "cfp.llm.tier2.get_available_models", lambda *a, **kw: list(models)
        )

        # Embedding stub — return a fixed 768-d vector or None.
        async def _embed_one(text: str):
            if embedding_vec is None:
                return [0.0] * 768
            return list(embedding_vec)

        monkeypatch.setattr("cfp.llm.tier2.embed.embed_one", _embed_one)
        monkeypatch.setattr(
            "cfp.llm.tier2.vec.is_duplicate",
            lambda v: is_duplicate_returns,
        )
        monkeypatch.setattr(
            "cfp.llm.tier2.vec.upsert_event_embedding",
            lambda *a, **kw: None,
        )
        return script

    return _set


def _seed_event(conn, *, notification: date | None = None) -> int:
    """Insert a minimal event and return its event_id."""
    event_id = db.insert_minimal_event(
        conn,
        acronym="ICML",
        name="International Conference on Machine Learning",
        categories=[],
        is_workshop=False,
        is_virtual=False,
        origin_url="https://icml.cc/Conferences/2026",
        source="icml.cc",
        scrape_session_id=None,
    )
    if notification is not None:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE events SET notification = %s WHERE event_id = %s",
                (notification, event_id),
            )
        conn.commit()
    return event_id


# ---------------------------------------------------------------------------
# Round-1 baseline payload (high confidence + null notification)
# ---------------------------------------------------------------------------

_R1_OK_NULL_NOTIF = {
    "acronym": "ICML",
    "name": "International Conference on Machine Learning",
    "edition_year": 2026,
    "is_workshop": False,
    "categories": ["ML"],
    "is_virtual": False,
    "description": "Premier ML conference",
    "rank": "A*",
    "when_raw": "Jul 18 - Jul 24, 2026",
    "start_date": "2026-07-18",
    "end_date": "2026-07-24",
    "where_raw": "Vienna, Austria",
    "country": "AT",
    "region": "Europe",
    "india_state": None,
    "abstract_deadline": "2026-01-23",
    "paper_deadline": "2026-01-30",
    "notification": None,    # ← LLM emits null
    "camera_ready": "2026-06-15",
    "official_url": "https://icml.cc",
    "submission_system": "OpenReview",
    "sponsor_names": ["IEEE"],
    "raw_tags": ["machine learning"],
    "confidence": 0.93,
}

_R2_OK = {"people": [], "confidence": 0.95}
_R3_OK = {
    "venue_name": "Vienna Convention Centre",
    "city": "Vienna", "state": None, "country": "AT", "region": "Europe",
    "address": None, "maps_url": None, "confidence": 0.92,
}
_R4_OK = {
    "name": "Institute of Electrical and Electronics Engineers",
    "short_name": "IEEE", "type": "publisher", "country": "US",
    "city": None, "homepage": "https://ieee.org", "confidence": 0.97,
}
_R5_OK = {"pass": True, "flags": [], "severity": "ok", "reason": None}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_tier2_high_confidence_coalesce_preserves_notification(db_conn, patch_t2):
    """Spec: COALESCE upsert keeps existing notification when LLM returns null."""
    seeded_notif = date(2026, 4, 15)
    event_id = _seed_event(db_conn, notification=seeded_notif)

    patch_t2([
        json.dumps(_R1_OK_NULL_NOTIF),
        json.dumps(_R2_OK),
        json.dumps(_R3_OK),
        json.dumps(_R4_OK),
        json.dumps(_R5_OK),
    ])

    result = asyncio.run(
        t2.run_tier2(event_id, "ICML 2026 page body — Vienna",
                     scrape_session_id=None,
                     source_url="https://icml.cc/Conferences/2026")
    )

    assert result.event_id == event_id
    assert result.deduped_to is None
    assert result.confidence == pytest.approx(0.92)  # min of the five rounds

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT notification, paper_deadline, country FROM events WHERE event_id = %s",
            (event_id,),
        )
        row = cur.fetchone()
    assert row["notification"] == seeded_notif       # COALESCE preserved
    assert row["paper_deadline"] == date(2026, 1, 30)
    assert row["country"] == "AT"


def test_tier2_long_context_routes_to_mistral_nemo(db_conn, patch_t2):
    """50k-token input → mistral-nemo:12b client.model captured on every call."""
    event_id = _seed_event(db_conn)

    long_text = "ICML 2026 " * 12_000  # ~24k words → well over 32k tokens
    script = patch_t2(
        [
            json.dumps(_R1_OK_NULL_NOTIF),
            json.dumps(_R2_OK),
            json.dumps(_R3_OK),
            json.dumps(_R4_OK),
            json.dumps(_R5_OK),
        ],
        available_models=["mistral-nemo:12b", "qwen3:14b-q4_K_M",
                          "nomic-embed-text"],
    )

    asyncio.run(
        t2.run_tier2(event_id, long_text, scrape_session_id=None,
                     source_url="https://icml.cc/Conferences/2026")
    )

    assert script.captured_models, "no chat calls captured"
    assert all(m == "mistral-nemo:12b" for m in script.captured_models)


def test_tier2_dedup_collapses_to_existing(db_conn, patch_t2):
    """is_duplicate returns an existing event_id → src is collapsed and no
    further rounds are attempted (Round 1 only)."""
    existing_id = _seed_event(db_conn)        # event 1
    src_id = _seed_event(db_conn)             # event 2 — incoming candidate

    script = patch_t2(
        [json.dumps(_R1_OK_NULL_NOTIF)],
        is_duplicate_returns=existing_id,
    )

    result = asyncio.run(
        t2.run_tier2(src_id, "ICML 2026 page body — Vienna",
                     scrape_session_id=None,
                     source_url="https://icml.cc/Conferences/2026")
    )

    assert result.deduped_to == existing_id
    assert result.event_id == src_id
    assert len(script.calls) == 1   # only round 1 ran

    with db_conn.cursor() as cur:
        cur.execute("SELECT superseded_by FROM events WHERE event_id = %s", (src_id,))
        row = cur.fetchone()
    assert row["superseded_by"] == existing_id


def test_tier2_quality_block_escalates_to_tier4(db_conn, patch_t2):
    event_id = _seed_event(db_conn)

    blocked_quality = {
        "pass": False,
        "flags": ["predatory_publisher"],
        "severity": "block",
        "reason": "predatory list match",
    }
    patch_t2([
        json.dumps(_R1_OK_NULL_NOTIF),
        json.dumps(_R2_OK),
        json.dumps(_R3_OK),
        json.dumps(_R4_OK),
        json.dumps(blocked_quality),
    ])

    with pytest.raises(TierEscalation) as excinfo:
        asyncio.run(
            t2.run_tier2(event_id, "shady CFP",
                         scrape_session_id=None,
                         source_url="https://shady.example.com")
        )
    assert excinfo.value.reason == "quality_block"
    assert excinfo.value.target_tier == 4


def test_tier2_low_confidence_escalates_to_tier3(db_conn, patch_t2):
    event_id = _seed_event(db_conn)

    low_r1 = dict(_R1_OK_NULL_NOTIF, confidence=0.55)
    patch_t2([
        json.dumps(low_r1),
        json.dumps(_R2_OK),
        json.dumps(_R3_OK),
        json.dumps(_R4_OK),
        json.dumps(_R5_OK),
    ])

    with pytest.raises(TierEscalation) as excinfo:
        asyncio.run(
            t2.run_tier2(event_id, "ambiguous CFP page",
                         scrape_session_id=None,
                         source_url="https://example.org/cfp")
        )
    assert excinfo.value.reason == "low_confidence"
    assert excinfo.value.target_tier == 3
