# Codegen 10 — cfp/llm/tier1.py + cfp/llm/tier2.py

## Files to Create
- `cfp/llm/tier1.py`
- `cfp/llm/tier2.py`
- `cfp/llm/_json_repair.py` (shared)
- `cfp/llm/_tokens.py` (shared)
- `cfp/llm/__init__.py` (exports `TierEscalation`)

## Rule
LLM **pipeline orchestrators**, not LLM clients. They sit on `cfp/llm/client.py`
(codegen 09) and combine: prompt assembly, JSON repair + retry policy,
confidence-based routing, dedup pre-check, COALESCE upsert into PostgreSQL,
embedding write, audit logging into `tier_runs`.

Tier 3/4 are v2 only.

`prompts.md` is the source of truth — read only via `cfp.prompts_parser.get_prompt(name)`.
Never embed prompt text in code.

---

## Imports (both files)

```python
from __future__ import annotations
import asyncio, json, time
from dataclasses import dataclass, field
from typing import Any, Optional

from config import (TIER_THRESHOLD, LONG_CONTEXT_TOKENS,
                    JSON_REPAIR_ENABLED, JSON_RETRY_SAME_TIER,
                    PARSE_FAIL_THRESHOLD)
from cfp.models import (Category, Tier, Event, Person, Venue, Organisation,
                        PersonRole, OrgType)
from cfp.llm.client import (OllamaClient, resolve_model, _parse_json_response,
                            _strip_thinking)
from cfp.prompts_parser import get_prompt
from cfp import db, queue, embed, dedup
```

---

## Shared exception (`cfp/llm/__init__.py`)

```python
class TierEscalation(Exception):
    def __init__(self, reason: str, target_tier: int) -> None:
        super().__init__(f"escalate→tier{target_tier}: {reason}")
        self.reason = reason
        self.target_tier = target_tier

ESCALATE_REASONS = frozenset({
    "low_confidence", "multi_category", "unknown_site", "long_context",
    "dedup_ambiguous", "ontology_edge", "json_parse_fail",
    "model_unavailable", "quality_block", "dedup_collapsed",
})
```

---

## Result dataclasses

```python
# tier1.py
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

# tier2.py
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
```

---

## JSON repair helper (`cfp/llm/_json_repair.py`)

```python
import re
from cfp.llm.client import _parse_json_response, _strip_thinking
from config import JSON_REPAIR_ENABLED

def repair_json(raw: str):
    if not JSON_REPAIR_ENABLED:
        return None
    text = _strip_thinking(raw)
    parsed = _parse_json_response(text)
    if parsed: return parsed
    candidate = re.sub(r"(?<=[\{,])\s*'([A-Za-z0-9_]+)'\s*:", r'"\1":', text)
    candidate = re.sub(r",(\s*[\}\]])", r"\1", candidate)
    parsed = _parse_json_response(candidate)
    if parsed: return parsed
    try:
        import json5
        return json5.loads(candidate)
    except Exception:
        return None
```

---

## Token counter (`cfp/llm/_tokens.py`)

```python
def count_tokens(text: str) -> int:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)
```

---

## Retry helper (per Q12)

```python
async def _invoke_with_retry(client, system, user_payload, *, target_tier_on_fail):
    """Q12 policy: parse → repair → 1 same-tier retry → escalate."""
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload)}]
    for attempt in range(JSON_RETRY_SAME_TIER + 1):
        raw = await asyncio.to_thread(client.chat, messages, None, "json", None)
        parsed = _parse_json_response(raw) or repair_json(raw)
        if parsed: return parsed
        await queue.incr_metric(f"cfp:metrics:parse_fail:{client.model}")
        messages = [
            {"role": "system",
             "content": ("Your previous response was not valid JSON. Return "
                         "ONE JSON object only, no prose, no code fences.\n\n" + system)},
            {"role": "user", "content": json.dumps(user_payload)},
        ]
    raise TierEscalation("json_parse_fail", target_tier=target_tier_on_fail)
```

---

## tier1.py — model: qwen3:4b-q4_K_M

### Routing rules
- `is_cfp == false` → log to tier_runs, return Tier1Result, no Event row
- `is_cfp == true AND confidence >= 0.85` → INSERT minimal Event row, push to cfp:queue:tier2
- `is_cfp == true AND confidence < 0.85` → INSERT minimal Event, push to cfp:escalate:tier2, raise TierEscalation
- JSON parse fail → repair → 1 retry → if still fail, raise TierEscalation("json_parse_fail", 2)

### Public API
```python
async def run_tier1(text: str, source_url: str, *, scrape_session_id: int) -> Tier1Result: ...
```

### Implementation
1. Build user_payload from text + source_url (cheap regex hints for acronym/name)
2. `_invoke_with_retry(client, PROMPT_TIER1, user_payload, target_tier_on_fail=2)`
3. Coerce output (defensive — model can lie)
4. If is_cfp=False: write tier_runs, return discard
5. Else: insert minimal event row → SERIAL event_id
6. Write tier_runs with escalate flag
7. If below threshold: push to escalate, raise TierEscalation
8. Else: push to tier2 queue, return Tier1Result

---

## tier2.py — model: qwen3:14b-q4_K_M (or mistral-nemo:12b on long context)

### Long-context routing
```python
def _pick_tier2_model(text, machine_models):
    if count_tokens(text) > LONG_CONTEXT_TOKENS:
        if "mistral-nemo:12b" in machine_models:
            return "mistral-nemo:12b"
        raise TierEscalation("long_context", target_tier=4)
    if any(m.startswith("qwen3:14b") for m in machine_models):
        return "qwen3:14b"
    raise TierEscalation("model_unavailable", target_tier=4)
```

### Prompt chain (sequential, single scrape session)
| Round | Prompt | Output |
|---|---|---|
| 1 | PROMPT_TIER2 | event_record |
| 2 | PROMPT_PERSON_EXTRACT | people: list[Person] |
| 3 | PROMPT_VENUE_EXTRACT (skip if is_virtual) | venue: Venue|None |
| 4 | PROMPT_ORG_EXTRACT (one per sponsor_name) | orgs: list[Organisation] |
| 5 | PROMPT_QUALITY_GUARD | quality_flags, quality_severity |

Confidence = min of all rounds' confidence.

### Routing
- `severity=="block"` → escalate to tier4 (cfp:dead in v1), raise TierEscalation
- `confidence < 0.85` → escalate to tier3, raise TierEscalation
- Else: dedup pre-check; if duplicate, collapse and return; else COALESCE upsert + embedding

### Dedup pre-check (hot path, no LLM in v1)
```python
candidate = _to_event(event_id, parsed_round1)
existing = await dedup.find_duplicate(candidate)  # cosine >= 0.97 only
if existing and existing != event_id:
    db.collapse_event(conn, src=event_id, dst=existing)
    return Tier2Result(deduped_to=existing, ...)
```

### Public API
```python
async def run_tier2(event_id: int, text: str, *, scrape_session_id: int) -> Tier2Result: ...
```

---

## DB API additions (cfp/db.py needs these)
```python
def insert_minimal_event(conn, *, acronym, name, categories, is_workshop,
                         is_virtual, origin_url, source, scrape_session_id) -> int: ...
def insert_tier_run(conn, *, event_id, tier, model, confidence, output_json,
                    escalate, escalate_reason, elapsed_ms, scrape_session_id) -> None: ...
def collapse_event(conn, *, src: int, dst: int) -> None: ...
```

---

## Queue API additions (cfp/queue.py needs these)
```python
async def push_tier2(*, event_id, text, source_url) -> None: ...
async def push_escalation(*, target_tier, event_id, reason, text) -> None: ...
async def incr_metric(key: str) -> None: ...
```

---

## Tests

### tests/test_tier1.py
- valid JSON inserts row, no escalation
- malformed JSON recovered via repair
- persistent malformed escalates to tier2 with json_parse_fail
- low confidence (0.5) escalates to tier2 with low_confidence

### tests/test_tier2.py
- High confidence + COALESCE upsert preserves notification when LLM emits null
- Long-context (50k tokens) routes to mistral-nemo:12b
- Dedup pre-check finds duplicate → collapses, returns existing event_id
- quality_severity=="block" raises TierEscalation("quality_block", 4)
- Confidence below threshold escalates to tier3

---

## Acceptance Criteria

- run_tier1 returns Tier1Result with event_id > 0 when is_cfp=True (any confidence)
- run_tier1 returns event_id=-1 when is_cfp=False; no events row inserted
- TierEscalation raised after Tier 1 row insertion (placeholder row exists)
- run_tier2 dedup pre-check fires BEFORE upsert
- COALESCE upsert via cfp.db.upsert_event preserves null-protected fields
- Embedding write is non-fatal (separate try/except)
- Long-context routing uses mistral-nemo:12b when available; else escalates to tier4

---

## Downstream Consumers

| Module | Usage |
|---|---|
| `cfp/pipeline.py` | run_tier1, run_tier2, catches TierEscalation |
| `cfp/cli.py` | invokes pipeline.py |
| Tests | Tier1Result, Tier2Result, TierEscalation |
