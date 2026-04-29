# Codegen 15 — cfp/dedup.py

## File to Create
- `cfp/dedup.py`
- `tests/test_dedup_unit.py`
- `tests/test_dedup_pg.py`

## Rule
v1 deduplication via **pgvector cosine only** — no LLM call. Hot path
(synchronous, on every Tier 2/3 write) does a single ANN lookup; ambiguous
pairs (0.92 ≤ cosine < 0.97) push to `cfp:dedup_pending` for v2's
DeepSeek-R1 confirmation worker. Cross-source dedup uses ANN top-K=5 +
acronym-year collision check as nightly sanity net.

This module is the **only** writer of `cfp:dedup_pending`. v2 will add
`cfp/dedup_worker.py`. Do not add LLM imports here in v1.

Reference: arch.md §1 Q2 (trigger timing), Q6 (cross-source blocking),
Q8 (IVFFlat strategy), §6 (v1 scope: pgvector cosine only).

---

## Imports
```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import json, re, time

import psycopg
from psycopg.rows import dict_row

from config import (PG_DSN, EMBED_DIM, DEDUP_COSINE, DEDUP_AUTO_MERGE,
                    DEDUP_TOP_K, REDIS_URL)
from cfp.db import get_conn
```

No `cfp.llm.*` imports in v1. No `cfp.queue` import — Redis client local.

---

## Constants
```python
DEDUP_QUEUE_KEY = "cfp:dedup_pending"
SUPERSEDED_BY_COL = "superseded_by"
```

### DDL prerequisite (cfp/db.py init_db)
```sql
ALTER TABLE events ADD COLUMN IF NOT EXISTS superseded_by INTEGER REFERENCES events(event_id);
CREATE INDEX IF NOT EXISTS idx_events_superseded_by ON events(superseded_by);
```

A row with `superseded_by IS NOT NULL` is the merge loser — keep for audit,
exclude from reports and future ANN candidate sets.

---

## Data classes
```python
@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: int
    cosine: float
    acronym: Optional[str]
    year: Optional[int]
    confidence: float
    reason: str   # "ann_top1" | "ann_topk" | "acronym_match"

@dataclass(slots=True)
class SweepReport:
    pairs_examined: int
    merges_applied: int
    pairs_queued_for_llm: int
    duration_seconds: float
```

`Candidate.cosine` is similarity (1 - cosine_distance), range [-1, 1] but typ. [0, 1].

---

## Public API
```python
def precheck_duplicate(vec: list[float]) -> Optional[int]: ...
    """Hot-path: cosine >= 0.97 → return existing event_id. 0.92 ≤ cosine < 0.97 → push to queue. Else None."""

def find_candidates(vec, *, top_k=5, exclude_event_id=None) -> list[Candidate]: ...
    """Top-K nearest neighbours with cosine >= DEDUP_COSINE. Excludes superseded."""

def merge_events(winner_id: int, loser_id: int, *, reason: str) -> None: ...
    """COALESCE-merge non-null fields; reroute event_people/event_organisations/tier_runs;
       delete loser embedding; mark loser superseded_by. Single transaction."""

def sweep() -> SweepReport: ...
    """Nightly: ANN top-K + acronym blocking; auto-merge above 0.97; queue 0.92-0.97."""

def acronym_blocking() -> list[tuple[int, int]]: ...
    """All (a, b) where a < b and normalise(acronym_a) == normalise(acronym_b) AND year matches."""
```

---

## precheck_duplicate
```python
def precheck_duplicate(vec):
    if len(vec) != EMBED_DIM:
        raise ValueError(f"expected {EMBED_DIM}-d vector")
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT e.event_id, 1 - (ee.vec <=> %s::vector) AS cosine
            FROM event_embeddings ee
            JOIN events e ON e.event_id = ee.event_id
            WHERE e.superseded_by IS NULL
            ORDER BY ee.vec <=> %s::vector LIMIT 1
        """, (vec, vec))
        row = cur.fetchone()
    if not row: return None
    cosine = float(row["cosine"])
    if cosine >= DEDUP_AUTO_MERGE:
        return int(row["event_id"])
    if cosine >= DEDUP_COSINE:
        _push_pending(existing_event_id=int(row["event_id"]),
                      cosine=cosine, new_vec=vec, reason="ann_topk_pending")
    return None
```

### Queue payload schema
```json
{"kind": "incoming|existing_pair",
 "existing_event_id": 123, "candidate_event_id": null,
 "candidate_vec": [0.01, ...], "cosine": 0.945,
 "reason": "ann_topk_pending|acronym_match|sweep",
 "ts": "2026-04-29T..."}
```

```python
def _push_pending(*, existing_event_id, cosine, new_vec=None,
                  candidate_event_id=None, reason):
    import redis
    payload = {
        "kind": "existing_pair" if candidate_event_id else "incoming",
        "existing_event_id": existing_event_id,
        "candidate_event_id": candidate_event_id,
        "candidate_vec": new_vec, "cosine": cosine,
        "reason": reason, "ts": _utc_now_iso(),
    }
    redis.from_url(REDIS_URL, decode_responses=True).rpush(
        DEDUP_QUEUE_KEY, json.dumps(payload))
```

---

## Acronym normalisation (mirrors PROMPT_DEDUP)
```python
_ORDINAL_RE = re.compile(r"^(\d+)(st|nd|rd|th)\s+", re.IGNORECASE)
_YEAR4_RE = re.compile(r"\s*(19|20)\d{2}\s*$")
_YEAR2_RE = re.compile(r"\s*\d{2}\s*$")
_PUNCT_RE = re.compile(r"[\-\._/'\"]")
_WS_RE = re.compile(r"\s+")

def _normalise_acronym(acr):
    if not acr: return ""
    s = acr.strip().lower()
    if s.startswith("the "): s = s[4:]
    s = _ORDINAL_RE.sub("", s)
    s = _YEAR4_RE.sub("", s); s = _YEAR2_RE.sub("", s)
    s = _PUNCT_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()
```

---

## merge_events — column-by-column COALESCE
```python
_MERGEABLE_COLS = (
    "acronym", "name", "series_id", "edition_year", "categories",
    "is_workshop", "is_virtual", "when_raw", "start_date", "end_date",
    "abstract_deadline", "paper_deadline", "notification", "camera_ready",
    "where_raw", "country", "region", "india_state", "venue_id",
    "origin_url", "official_url", "submission_system", "sponsor_names",
    "raw_tags", "description", "rank", "source",
    "quality_flags", "quality_severity",
)
# Excluded: event_id (PK), scraped_at (winner's), last_checked (NOW()),
# superseded_by, notes (concatenated), scrape_session_id

def merge_events(winner_id, loser_id, *, reason):
    if winner_id == loser_id:
        raise ValueError("winner_id == loser_id")
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Lock both rows
            cur.execute("SELECT event_id, superseded_by FROM events "
                        "WHERE event_id IN (%s, %s) FOR UPDATE",
                        (winner_id, loser_id))
            rows = {r["event_id"]: r for r in cur.fetchall()}
            if winner_id not in rows or loser_id not in rows:
                raise ValueError("winner or loser not found")
            if rows[winner_id]["superseded_by"] is not None:
                raise ValueError(f"winner {winner_id} is itself superseded")
            if rows[loser_id]["superseded_by"] is not None:
                raise ValueError(f"loser {loser_id} already superseded")

            # 1. COALESCE-merge from loser
            set_clause = ", ".join(f"{c} = COALESCE(w.{c}, l.{c})"
                                   for c in _MERGEABLE_COLS)
            cur.execute(f"""
                UPDATE events w SET {set_clause},
                    notes = CASE WHEN COALESCE(l.notes,'') = '' THEN w.notes
                                 ELSE COALESCE(w.notes,'') || E'\\n--- merged from '
                                      || l.event_id || E' ---\\n' || l.notes END,
                    last_checked = NOW()
                FROM events l
                WHERE w.event_id = %s AND l.event_id = %s
            """, (winner_id, loser_id))

            # 2. Reroute child FKs
            for tbl in ("event_people", "event_organisations"):
                cur.execute(f"""
                    UPDATE {tbl} ep SET event_id = %s
                    WHERE ep.event_id = %s AND NOT EXISTS (
                        SELECT 1 FROM {tbl} ep2 WHERE ep2.event_id = %s
                          AND ep2.person_id IS NOT DISTINCT FROM ep.person_id
                          AND ep2.org_id IS NOT DISTINCT FROM ep.org_id
                          AND ep2.role = ep.role
                    )
                """, (winner_id, loser_id, winner_id))
                cur.execute(f"DELETE FROM {tbl} WHERE event_id = %s", (loser_id,))
            cur.execute("UPDATE tier_runs SET event_id = %s WHERE event_id = %s",
                        (winner_id, loser_id))

            # 3. Drop loser embedding
            cur.execute("DELETE FROM event_embeddings WHERE event_id = %s",
                        (loser_id,))

            # 4. Mark loser superseded
            cur.execute("""
                UPDATE events SET superseded_by = %s,
                    notes = COALESCE(notes,'') || E'\\n[superseded by '
                            || %s || ': ' || %s || ']'
                WHERE event_id = %s
            """, (winner_id, winner_id, reason, loser_id))
        conn.commit()
```

---

## sweep
```python
def sweep() -> SweepReport:
    started = time.monotonic()
    report = SweepReport(0, 0, 0, 0.0)

    # 1. ANN-driven pairs
    ann_pairs = set()
    cosine_for = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT e.event_id FROM events e
            JOIN event_embeddings ee ON ee.event_id = e.event_id
            WHERE e.superseded_by IS NULL
        """)
        all_ids = [r["event_id"] for r in cur.fetchall()]
    for eid in all_ids:
        cands = find_candidates_for_event(eid, top_k=DEDUP_TOP_K)
        for c in cands:
            lo, hi = sorted([eid, c.candidate_id])
            ann_pairs.add((lo, hi))
            cosine_for[(lo, hi)] = c.cosine

    # 2. Acronym block pairs
    acr_pairs = set(acronym_blocking())
    all_pairs = ann_pairs | acr_pairs
    report.pairs_examined = len(all_pairs)

    # 3. Decide per pair
    merged = set()
    for (a, b) in sorted(all_pairs):
        if a in merged or b in merged: continue
        cos = cosine_for.get((a, b)) or _fetch_cosine(a, b)
        if cos is not None and cos >= DEDUP_AUTO_MERGE:
            with get_conn() as conn:
                w, l = _pick_winner(conn, a, b)
            merge_events(w, l, reason=f"sweep_auto cosine={cos:.3f}")
            merged.add(l)
            report.merges_applied += 1
        elif (cos is not None and cos >= DEDUP_COSINE) or (a, b) in acr_pairs:
            _push_pending(existing_event_id=a, candidate_event_id=b,
                          cosine=cos or 0.0,
                          reason="sweep" if cos else "acronym_match")
            report.pairs_queued_for_llm += 1
    report.duration_seconds = time.monotonic() - started
    return report

def _pick_winner(conn, a, b):
    """Highest confidence wins; tiebreak on most recent scraped_at."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT e.event_id, COALESCE(MAX(tr.confidence), 0.0) AS confidence,
                   e.scraped_at
            FROM events e
            LEFT JOIN tier_runs tr ON tr.event_id = e.event_id
            WHERE e.event_id = ANY(%s)
            GROUP BY e.event_id, e.scraped_at
        """, ([a, b],))
        rows = sorted(cur.fetchall(),
                      key=lambda r: (-r["confidence"], -r["scraped_at"].timestamp()))
    return rows[0]["event_id"], rows[1]["event_id"]
```

---

## acronym_blocking
```python
def acronym_blocking():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT event_id, acronym, edition_year FROM events
            WHERE superseded_by IS NULL
              AND acronym IS NOT NULL AND edition_year IS NOT NULL
        """)
        rows = cur.fetchall()
    by_key = {}
    for r in rows:
        key = (_normalise_acronym(r["acronym"]), r["edition_year"])
        if not key[0]: continue
        by_key.setdefault(key, []).append(r["event_id"])
    pairs = []
    for ids in by_key.values():
        if len(ids) < 2: continue
        ids = sorted(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.append((ids[i], ids[j]))
    return pairs
```

---

## Tests

### tests/test_dedup_unit.py (no PG)
```python
@pytest.mark.parametrize("raw, expected", [
    ("ICRA-2025", "icra"), ("ICRA 25", "icra"), ("ICRA", "icra"),
    ("12th ICML 2025", "icml"), ("the NeurIPS", "neurips"),
    ("ACM/SIGCHI'24", "acmsigchi"), ("", ""), (None, ""),
    ("CVPR.2025", "cvpr"), ("21st AAAI", "aaai"),
])
def test_normalise_acronym(raw, expected):
    assert _normalise_acronym(raw) == expected
```

### tests/test_dedup_pg.py (integration, marker `pg`)
- precheck cosine 0.99 → returns existing id, no queue push
- precheck cosine 0.95 → returns None + 1 queue entry
- precheck cosine 0.85 → returns None + 0 queue activity
- precheck skips superseded rows
- merge_events lossless field copy + supersede mark
- merge_events reroutes event_people FKs; PK collision safe
- merge_events rejects already-superseded loser
- acronym_blocking finds pairwise (3 events with same acronym → 3 pairs)
- sweep on 50 events + 5 known dups → finds all 5

```ini
# pyproject.toml
[tool.pytest.ini_options]
markers = ["pg: requires running cfp_postgres container"]
```

---

## Acceptance Criteria

- precheck_duplicate is single SQL round-trip; **zero LLM calls in v1**.
- Cosine 0.99 → returns id; 0.95 → None+queue; 0.85 → None+silent.
- merge_events transactional; raises ValueError on already-superseded.
- acronym_blocking matches PROMPT_DEDUP normalisation byte-for-byte.
- sweep on 50+5 fixture finds all 5; merges_applied >= 5.
- superseded_by IS NOT NULL rows excluded from all read paths.

---

## Downstream Consumers

| Module | Usage |
|---|---|
| `cfp/db.py` | precheck_duplicate inside upsert_event before INSERT |
| `cfp/cli.py` | sweep() for `cfp dedup-sweep` |
| `cfp/analytics.py` | reports filter `WHERE superseded_by IS NULL` |
| (v2) `cfp/dedup_worker.py` | drains cfp:dedup_pending; calls merge_events on confirmed matches |

---

## v2 follow-ups (NOT v1)
1. `cfp/dedup_worker.py` — DeepSeek-R1 confirmation worker
2. `cfp dedup-sweep --dry-run` mode
3. Recall benchmark on labelled set
4. Transitive supersede chain following
