"""Tier 1 — fast triage classifier (codegen/10).

Model: qwen3:4b-q4_K_M (via resolve_model("tier1")).

Responsibilities:
    1. Run PROMPT_TIER1 on a (text, source_url) pair.
    2. Defensively coerce the LLM JSON.
    3. Drop non-CFP rows (no events row, audit only).
    4. INSERT a minimal Event row for genuine CFPs and obtain SERIAL event_id.
    5. Push to cfp:queue:tier2 (high confidence) or cfp:escalate:tier2
       (low confidence) and log a tier_runs entry either way.

The handler raises :class:`cfp.llm.TierEscalation` to signal the dispatcher;
the row insert always happens BEFORE the raise so escalations carry a real
event_id forward.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

from config import JSON_RETRY_SAME_TIER, TIER_THRESHOLD
from cfp import db, queue
from cfp.llm import TierEscalation
from cfp.llm._json_repair import repair_json
from cfp.llm.client import OllamaClient, _parse_json_response
from cfp.models import Category
from cfp.prompts_parser import get_prompt


_TIER1_MODEL_ALIAS = "tier1"

# Cheap heuristics so the model has a head-start. We extract a candidate
# acronym (first ALL-CAPS token of length 2-12 with optional year) and a
# candidate name (first non-empty line under ~120 chars). Both are advisory
# — the model is free to override.
_ACRONYM_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,11}(?:[ -]?20\d{2})?)\b")
_TIER1_TEXT_LIMIT = 4_000   # truncate to keep the 4b model in its sweet spot


# ---------------------------------------------------------------------------
# Result + helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Tier1Result:
    event_id: int
    is_cfp: bool
    is_workshop: bool
    categories: list[Category]
    is_virtual: bool
    confidence: float
    latency_ms: int
    raw_output: dict = field(default_factory=dict)


def _domain_of(url: Optional[str]) -> str:
    if not url:
        return ""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _hint_acronym(text: str) -> str:
    m = _ACRONYM_RE.search(text or "")
    return m.group(1) if m else ""


def _hint_name(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if 5 <= len(line) <= 200:
            return line
    return (text or "")[:120].strip()


def _coerce_categories(raw: Any) -> list[Category]:
    """Defensively map model output to ``Category`` enum values.

    The model is told to return a closed-set list; any unrecognised string is
    silently dropped — the row will simply have an empty categories array.
    """
    if not isinstance(raw, list):
        return []
    valid = {c.value: c for c in Category}
    out: list[Category] = []
    for tok in raw:
        if isinstance(tok, str) and tok in valid and valid[tok] not in out:
            out.append(valid[tok])
    return out


def _coerce_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "y"}
    if isinstance(v, (int, float)):
        return bool(v)
    return default


def _coerce_confidence(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


# ---------------------------------------------------------------------------
# Retry + repair (Q12 policy — codegen/10 §Retry helper)
# ---------------------------------------------------------------------------


async def _invoke_with_retry(
    client: OllamaClient,
    system: str,
    user_payload: dict,
    *,
    target_tier_on_fail: int,
) -> dict:
    """Call ``client.chat`` once + (JSON_RETRY_SAME_TIER) retries.

    Each retry prepends a stricter system header. Any total failure escalates
    via :class:`TierEscalation`.
    """
    base_messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload, default=str)},
    ]
    messages = base_messages
    for attempt in range(JSON_RETRY_SAME_TIER + 1):
        raw = await asyncio.to_thread(
            client.chat, messages, tools=None, format="json", options=None
        )
        parsed = _parse_json_response(raw) or repair_json(raw)
        if isinstance(parsed, dict):
            return parsed
        # bump metric, sharpen system prompt, retry
        try:
            await queue.incr_metric(f"cfp:metrics:parse_fail:{client.model}")
        except Exception:
            pass
        messages = [
            {
                "role": "system",
                "content": (
                    "Your previous response was not valid JSON. Return ONE JSON "
                    "object only, no prose, no code fences.\n\n" + system
                ),
            },
            base_messages[1],
        ]
    raise TierEscalation("json_parse_fail", target_tier=target_tier_on_fail)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_tier1(
    text: str,
    source_url: str,
    *,
    scrape_session_id: Optional[str] = None,
) -> Tier1Result:
    """Triage ``text`` → JSON + DB row. Raises ``TierEscalation`` when the
    classifier is uncertain or its output cannot be repaired."""
    started = time.monotonic()
    system = get_prompt("PROMPT_TIER1")
    user_payload = {
        "acronym":       _hint_acronym(text),
        "name":          _hint_name(text),
        "raw_tags":      [],
        "snippet":       (text or "")[:_TIER1_TEXT_LIMIT],
        "source_domain": _domain_of(source_url),
    }

    client = OllamaClient(_TIER1_MODEL_ALIAS)

    parsed = await _invoke_with_retry(
        client, system, user_payload, target_tier_on_fail=2,
    )

    is_cfp      = _coerce_bool(parsed.get("is_cfp"))
    is_workshop = _coerce_bool(parsed.get("is_workshop"))
    is_virtual  = _coerce_bool(parsed.get("is_virtual"))
    categories  = _coerce_categories(parsed.get("categories"))
    confidence  = _coerce_confidence(parsed.get("confidence"))
    elapsed_ms  = int((time.monotonic() - started) * 1000)

    # ── non-CFP: audit only, no events row ──
    if not is_cfp:
        with db.get_conn() as conn:
            db.insert_tier_run(
                conn,
                event_id=None,
                tier=1,
                model=client.model,
                confidence=confidence,
                output_json=parsed,
                escalate=False,
                escalate_reason=None,
                elapsed_ms=elapsed_ms,
                scrape_session_id=scrape_session_id,
            )
        return Tier1Result(
            event_id=-1,
            is_cfp=False,
            is_workshop=is_workshop,
            categories=categories,
            is_virtual=is_virtual,
            confidence=confidence,
            latency_ms=elapsed_ms,
            raw_output=parsed,
        )

    # ── CFP: insert minimal row first so the event_id exists for routing ──
    acronym = (user_payload["acronym"] or _hint_acronym(text) or "UNKNOWN")[:64]
    name = (user_payload["name"] or acronym)[:512]
    with db.get_conn() as conn:
        event_id = db.insert_minimal_event(
            conn,
            acronym=acronym,
            name=name,
            categories=categories,
            is_workshop=is_workshop,
            is_virtual=is_virtual,
            origin_url=source_url,
            source=_domain_of(source_url) or "unknown",
            scrape_session_id=scrape_session_id,
        )

        below_threshold = confidence < TIER_THRESHOLD[1]
        db.insert_tier_run(
            conn,
            event_id=event_id,
            tier=1,
            model=client.model,
            confidence=confidence,
            output_json=parsed,
            escalate=below_threshold,
            escalate_reason="low_confidence" if below_threshold else None,
            elapsed_ms=elapsed_ms,
            scrape_session_id=scrape_session_id,
        )

    result = Tier1Result(
        event_id=event_id,
        is_cfp=True,
        is_workshop=is_workshop,
        categories=categories,
        is_virtual=is_virtual,
        confidence=confidence,
        latency_ms=elapsed_ms,
        raw_output=parsed,
    )

    if below_threshold:
        try:
            await queue.push_escalation(
                target_tier=2,
                event_id=event_id,
                reason="low_confidence",
                text=text,
                source_url=source_url,
            )
        except Exception:
            pass
        raise TierEscalation("low_confidence", target_tier=2)

    try:
        await queue.push_tier2(event_id=event_id, text=text, source_url=source_url)
    except Exception:
        pass

    return result
