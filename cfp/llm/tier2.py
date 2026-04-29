"""Tier 2 — structured extraction (codegen/10).

Default model: qwen3:14b-q4_K_M.
Long-context routing: mistral-nemo:12b once ``count_tokens(text)`` exceeds
``LONG_CONTEXT_TOKENS``; if the long-context model is unavailable we
escalate to tier 4.

Five-round prompt chain executed sequentially against the SAME client:
    1. PROMPT_TIER2          → main event extraction
    2. PROMPT_PERSON_EXTRACT → committee people
    3. PROMPT_VENUE_EXTRACT  → physical venue (skipped if is_virtual)
    4. PROMPT_ORG_EXTRACT    → one call per sponsor mention
    5. PROMPT_QUALITY_GUARD  → quality flags + severity verdict

Confidence is the MIN of every round that ran.

Routing:
    - severity == "block"     → escalate to tier 4 (cfp:dead in v1)
    - confidence < TIER_THRESHOLD[2] → escalate to tier 3
    - otherwise: dedup pre-check via cosine >= DEDUP_AUTO_MERGE; on hit
      collapse this event_id into the existing row and return early.
      On miss: COALESCE upsert + best-effort embedding write.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from config import LONG_CONTEXT_TOKENS, TIER_THRESHOLD
from cfp import db, embed, queue
from cfp import vectors as vec
from cfp.llm import TierEscalation
from cfp.llm._json_repair import repair_json
from cfp.llm._tokens import count_tokens
from cfp.llm.client import (
    OllamaClient,
    _parse_json_response,
    get_available_models,
)
from cfp.models import (
    Category,
    Event,
    Organisation,
    OrgType,
    Person,
    PersonRole,
    Venue,
)
from cfp.prompts_parser import get_prompt
from cfp.llm.tier1 import (
    _coerce_bool,
    _coerce_categories,
    _coerce_confidence,
    _invoke_with_retry,
)


_TIER2_MODEL_ALIAS = "qwen3:14b"
_LONG_CONTEXT_MODEL = "mistral-nemo:12b"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Tier2Result:
    event_id: int
    person_ids: list[int]
    venue_id: Optional[int]
    org_ids: list[int]
    embedding_written: bool
    confidence: float
    latency_ms: int
    raw_output: dict = field(default_factory=dict)
    deduped_to: Optional[int] = None


# ---------------------------------------------------------------------------
# Routing helper
# ---------------------------------------------------------------------------


def _pick_tier2_model(text: str, machine_models: list[str]) -> str:
    """Return the model alias to use for this Tier-2 input.

    Long inputs (> LONG_CONTEXT_TOKENS) require ``mistral-nemo:12b``; if it
    isn't pulled on this machine we escalate. Short inputs go to the default
    qwen3:14b — any quant tag accepted.
    """
    if count_tokens(text) > LONG_CONTEXT_TOKENS:
        if any(m.startswith(_LONG_CONTEXT_MODEL) for m in machine_models):
            return _LONG_CONTEXT_MODEL
        raise TierEscalation("long_context", target_tier=4)
    if any(m.startswith("qwen3:14b") for m in machine_models):
        return _TIER2_MODEL_ALIAS
    raise TierEscalation("model_unavailable", target_tier=4)


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def _coerce_int(v: Any) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return str(v)


def _coerce_str_list(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    return [s.strip() for s in v if isinstance(s, str) and s.strip()]


def _coerce_date(v: Any) -> Optional[date]:
    if not v or not isinstance(v, str):
        return None
    try:
        return datetime.strptime(v.strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _coerce_role(v: Any) -> PersonRole:
    if isinstance(v, str):
        try:
            return PersonRole(v.strip())
        except ValueError:
            return PersonRole.OTHER
    return PersonRole.OTHER


def _coerce_org_type(v: Any) -> OrgType:
    if isinstance(v, str):
        try:
            return OrgType(v.strip())
        except ValueError:
            return OrgType.OTHER
    return OrgType.OTHER


# ---------------------------------------------------------------------------
# Round 1 helper — promote parsed dict to Event
# ---------------------------------------------------------------------------


def _to_event(event_id: int, parsed: dict, *, source_url: Optional[str],
              scrape_session_id: Optional[str]) -> Event:
    return Event(
        event_id=event_id,
        acronym=(_coerce_str(parsed.get("acronym")) or "UNKNOWN")[:64],
        name=(_coerce_str(parsed.get("name")) or "UNKNOWN")[:512],
        edition_year=_coerce_int(parsed.get("edition_year")),
        categories=_coerce_categories(parsed.get("categories")),
        is_workshop=_coerce_bool(parsed.get("is_workshop")),
        is_virtual=_coerce_bool(parsed.get("is_virtual")),
        when_raw=_coerce_str(parsed.get("when_raw")),
        start_date=_coerce_date(parsed.get("start_date")),
        end_date=_coerce_date(parsed.get("end_date")),
        abstract_deadline=_coerce_date(parsed.get("abstract_deadline")),
        paper_deadline=_coerce_date(parsed.get("paper_deadline")),
        notification=_coerce_date(parsed.get("notification")),
        camera_ready=_coerce_date(parsed.get("camera_ready")),
        where_raw=_coerce_str(parsed.get("where_raw")),
        country=_coerce_str(parsed.get("country")),
        region=_coerce_str(parsed.get("region")),
        india_state=_coerce_str(parsed.get("india_state")),
        description=_coerce_str(parsed.get("description")),
        rank=_coerce_str(parsed.get("rank")),
        official_url=_coerce_str(parsed.get("official_url")),
        submission_system=_coerce_str(parsed.get("submission_system")),
        sponsor_names=_coerce_str_list(parsed.get("sponsor_names")),
        raw_tags=_coerce_str_list(parsed.get("raw_tags")),
        origin_url=source_url,
        scrape_session_id=scrape_session_id,
    )


def _embedding_text(parsed: dict) -> str:
    """Concatenate the few fields that matter for dedup similarity."""
    parts: list[str] = []
    for key in ("acronym", "name", "description", "where_raw", "when_raw"):
        v = parsed.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    cats = parsed.get("categories")
    if isinstance(cats, list):
        parts.extend(c for c in cats if isinstance(c, str))
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_tier2(
    event_id: int,
    text: str,
    *,
    scrape_session_id: Optional[str] = None,
    source_url: Optional[str] = None,
) -> Tier2Result:
    """Run the 5-round Tier-2 chain. May raise :class:`TierEscalation`.

    On success, the events row is upsert-COALESCED and an embedding is
    written (best-effort). On dedup hit, the row passed in is collapsed
    into the existing duplicate and the existing event_id is returned
    via ``deduped_to``.
    """
    started = time.monotonic()

    # ── pick model based on machine roster ──
    machine_models = get_available_models()
    model_alias = _pick_tier2_model(text, machine_models)
    client = OllamaClient(model_alias)

    # ── round 1: main extraction ──
    r1_payload = {
        "html_text":  text,
        "origin_url": source_url or "",
        "tier1":      {},
    }
    parsed1 = await _invoke_with_retry(
        client, get_prompt("PROMPT_TIER2"), r1_payload, target_tier_on_fail=3,
    )

    confidences: list[float] = [_coerce_confidence(parsed1.get("confidence"))]
    is_virtual = _coerce_bool(parsed1.get("is_virtual"))
    sponsor_names = _coerce_str_list(parsed1.get("sponsor_names"))

    # ── dedup pre-check (BEFORE persisting tier-2 fields) ──
    candidate = _to_event(
        event_id, parsed1,
        source_url=source_url, scrape_session_id=scrape_session_id,
    )
    dedup_text = _embedding_text(parsed1) or text[:2000]
    text_hash = hashlib.sha1(dedup_text.encode("utf-8")).hexdigest()
    candidate_vec: Optional[list[float]] = None
    try:
        candidate_vec = await embed.embed_one(dedup_text)
    except Exception:
        candidate_vec = None

    if candidate_vec is not None:
        try:
            existing = vec.is_duplicate(candidate_vec)
        except Exception:
            existing = None
        if existing is not None and existing != event_id:
            with db.get_conn() as conn:
                db.collapse_event(conn, src=event_id, dst=existing)
                db.insert_tier_run(
                    conn,
                    event_id=existing,
                    tier=2,
                    model=client.model,
                    confidence=confidences[0],
                    output_json={"deduped_to": existing,
                                 "src_event_id": event_id,
                                 "round1": parsed1},
                    escalate=False,
                    escalate_reason="dedup_collapsed",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    scrape_session_id=scrape_session_id,
                )
            return Tier2Result(
                event_id=event_id,
                person_ids=[],
                venue_id=None,
                org_ids=[],
                embedding_written=False,
                confidence=confidences[0],
                latency_ms=int((time.monotonic() - started) * 1000),
                raw_output={"round1": parsed1},
                deduped_to=existing,
            )

    # ── round 2: people ──
    r2_payload = {
        "html_text":          text,
        "conference_acronym": candidate.acronym,
        "edition_year":       candidate.edition_year,
    }
    parsed2 = await _invoke_with_retry(
        client, get_prompt("PROMPT_PERSON_EXTRACT"),
        r2_payload, target_tier_on_fail=3,
    )
    confidences.append(_coerce_confidence(parsed2.get("confidence")))

    # ── round 3: venue (skip if virtual) ──
    parsed3: Optional[dict] = None
    if not is_virtual:
        r3_payload = {
            "html_text":  text,
            "where_raw":  candidate.where_raw,
            "is_virtual": is_virtual,
        }
        parsed3 = await _invoke_with_retry(
            client, get_prompt("PROMPT_VENUE_EXTRACT"),
            r3_payload, target_tier_on_fail=3,
        )
        confidences.append(_coerce_confidence(parsed3.get("confidence")))

    # ── round 4: orgs (one call per sponsor) ──
    parsed4_list: list[dict] = []
    for mention in sponsor_names:
        r4_payload = {"mention": mention, "context": text[:2000]}
        try:
            parsed4 = await _invoke_with_retry(
                client, get_prompt("PROMPT_ORG_EXTRACT"),
                r4_payload, target_tier_on_fail=3,
            )
        except TierEscalation:
            # Per-sponsor JSON failure shouldn't kill the whole pipeline; skip.
            continue
        parsed4_list.append(parsed4)
        confidences.append(_coerce_confidence(parsed4.get("confidence")))

    # ── round 5: quality guard ──
    r5_payload = {
        "event":       parsed1,
        "source_url":  source_url or "",
        "tier_result": {"confidence": min(confidences)},
    }
    parsed5 = await _invoke_with_retry(
        client, get_prompt("PROMPT_QUALITY_GUARD"),
        r5_payload, target_tier_on_fail=3,
    )
    severity = (parsed5.get("severity") or "ok") if isinstance(parsed5, dict) else "ok"

    confidence = min(confidences) if confidences else 0.0
    elapsed_ms = int((time.monotonic() - started) * 1000)

    raw_output = {
        "round1": parsed1,
        "round2": parsed2,
        "round3": parsed3,
        "round4": parsed4_list,
        "round5": parsed5,
    }

    # ── routing: block → tier 4 ──
    if severity == "block":
        with db.get_conn() as conn:
            db.insert_tier_run(
                conn,
                event_id=event_id,
                tier=2,
                model=client.model,
                confidence=confidence,
                output_json=raw_output,
                escalate=True,
                escalate_reason="quality_block",
                elapsed_ms=elapsed_ms,
                scrape_session_id=scrape_session_id,
            )
        try:
            await queue.push_escalation(
                target_tier=4, event_id=event_id,
                reason="quality_block", text=text, source_url=source_url,
            )
        except Exception:
            pass
        raise TierEscalation("quality_block", target_tier=4)

    # ── routing: low confidence → tier 3 ──
    if confidence < TIER_THRESHOLD[2]:
        with db.get_conn() as conn:
            db.insert_tier_run(
                conn,
                event_id=event_id,
                tier=2,
                model=client.model,
                confidence=confidence,
                output_json=raw_output,
                escalate=True,
                escalate_reason="low_confidence",
                elapsed_ms=elapsed_ms,
                scrape_session_id=scrape_session_id,
            )
        try:
            await queue.push_escalation(
                target_tier=3, event_id=event_id,
                reason="low_confidence", text=text, source_url=source_url,
            )
        except Exception:
            pass
        raise TierEscalation("low_confidence", target_tier=3)

    # ── happy path: persist event + auxiliary entities ──
    quality_flags = parsed5.get("flags") if isinstance(parsed5, dict) else []
    candidate.quality_flags = (
        quality_flags if isinstance(quality_flags, list) else []
    )
    candidate.quality_severity = severity if isinstance(severity, str) else None

    person_ids: list[int] = []
    org_ids: list[int] = []
    venue_id: Optional[int] = None
    embedding_written = False

    with db.get_conn() as conn:
        db.upsert_event(conn, candidate)

        for p in (parsed2.get("people") or [] if isinstance(parsed2, dict) else []):
            if not isinstance(p, dict):
                continue
            full_name = _coerce_str(p.get("full_name"))
            if not full_name:
                continue
            person = Person(
                person_id=0,
                full_name=full_name,
                email=_coerce_str(p.get("email")),
                homepage=_coerce_str(p.get("homepage")),
            )
            try:
                pid = db.upsert_person(conn, person)
                role = _coerce_role(p.get("role")).value
                db.link_event_person(conn, event_id, pid, role)
                person_ids.append(pid)
            except Exception:
                continue

        if parsed3 and isinstance(parsed3, dict):
            v_obj = Venue(
                venue_id=0,
                name=_coerce_str(parsed3.get("venue_name")),
                city=_coerce_str(parsed3.get("city")),
                state=_coerce_str(parsed3.get("state")),
                country=_coerce_str(parsed3.get("country")),
            )
            try:
                venue_id = db.upsert_venue(conn, v_obj)
            except Exception:
                venue_id = None

        for org in parsed4_list:
            if not isinstance(org, dict):
                continue
            name = _coerce_str(org.get("name"))
            if not name:
                continue
            org_obj = Organisation(
                org_id=0,
                name=name,
                type=_coerce_org_type(org.get("type")),
                country=_coerce_str(org.get("country")),
                website=_coerce_str(org.get("homepage")),
            )
            try:
                oid = db.upsert_org(conn, org_obj)
                db.link_event_org(conn, event_id, oid, "sponsor")
                org_ids.append(oid)
            except Exception:
                continue

        db.insert_tier_run(
            conn,
            event_id=event_id,
            tier=2,
            model=client.model,
            confidence=confidence,
            output_json=raw_output,
            escalate=False,
            escalate_reason=None,
            elapsed_ms=elapsed_ms,
            scrape_session_id=scrape_session_id,
        )

    # Embedding write is non-fatal (acceptance criterion).
    if candidate_vec is not None:
        try:
            vec.upsert_event_embedding(event_id, candidate_vec, text_hash)
            embedding_written = True
        except Exception:
            embedding_written = False

    return Tier2Result(
        event_id=event_id,
        person_ids=person_ids,
        venue_id=venue_id,
        org_ids=org_ids,
        embedding_written=embedding_written,
        confidence=confidence,
        latency_ms=int((time.monotonic() - started) * 1000),
        raw_output=raw_output,
        deduped_to=None,
    )
