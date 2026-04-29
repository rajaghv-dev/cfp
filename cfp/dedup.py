"""Deduplication for the CFP pipeline (codegen/15).

v1 strategy: pgvector cosine ONLY -- no LLM call. The hot path
(``precheck_duplicate``) runs a single ANN lookup at every Tier 2 / Tier 3
write; ambiguous pairs in the grey zone (``DEDUP_COSINE`` <= cosine <
``DEDUP_AUTO_MERGE``) are pushed to ``cfp:dedup_pending`` for v2's
DeepSeek-R1 confirmation worker.

This module is the only writer of ``cfp:dedup_pending`` in v1. v2 will add
``cfp/dedup_worker.py`` to drain that queue.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config import (
    DEDUP_AUTO_MERGE,
    DEDUP_COSINE,
    DEDUP_TOP_K,
    EMBED_DIM,
    REDIS_URL,
)
from cfp import vectors
from cfp.db import get_conn


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEDUP_QUEUE_KEY = "cfp:dedup_pending"
SUPERSEDED_BY_COL = "superseded_by"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: int
    cosine: float
    acronym: Optional[str]
    year: Optional[int]
    confidence: float
    reason: str  # "ann_top1" | "ann_topk" | "acronym_match"


@dataclass(slots=True)
class SweepReport:
    pairs_examined: int
    merges_applied: int
    pairs_queued_for_llm: int
    duration_seconds: float


# ---------------------------------------------------------------------------
# Acronym normalisation (mirrors PROMPT_DEDUP)
# ---------------------------------------------------------------------------

# "12th " / "21st " / etc. — leading ordinal token.
_ORDINAL_RE = re.compile(r"^(\d+)(st|nd|rd|th)\s+", re.IGNORECASE)
# Trailing 4-digit year (1900-2099).
_YEAR4_RE = re.compile(r"\s*(?:19|20)\d{2}\s*$")
# Trailing 2-digit year (e.g. "ICRA 25").
_YEAR2_RE = re.compile(r"\s*\d{2}\s*$")
# Punctuation classes we strip (hyphen, dot, underscore, slash, quotes).
_PUNCT_RE = re.compile(r"[\-\._/'\"]")
_WS_RE = re.compile(r"\s+")


def _normalise_acronym(acr: Optional[str]) -> str:
    """Lowercase, strip leading "the ", strip ordinals, strip year suffix,
    drop punctuation, collapse whitespace.

    Mirrors PROMPT_DEDUP's normalisation rules byte-for-byte so the SQL
    blocking step matches what the LLM is told to consider equivalent.
    """
    if not acr:
        return ""
    s = acr.strip().lower()
    if s.startswith("the "):
        s = s[4:]
    s = _ORDINAL_RE.sub("", s)
    s = _YEAR4_RE.sub("", s)
    s = _YEAR2_RE.sub("", s)
    s = _PUNCT_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_dim(vec) -> list[float]:
    v = list(vec)
    if len(v) != EMBED_DIM:
        raise ValueError(f"expected {EMBED_DIM}-d vector, got {len(v)}")
    return v


def _push_pending(
    *,
    existing_event_id: int,
    cosine: float,
    new_vec: Optional[list[float]] = None,
    candidate_event_id: Optional[int] = None,
    reason: str,
) -> None:
    """Push a grey-zone candidate to ``cfp:dedup_pending`` for v2 review.

    Lazy redis import keeps this module DB-only at import time.
    """
    import redis  # local import: cfp.dedup must not pull redis at import

    payload = {
        "kind": "existing_pair" if candidate_event_id is not None else "incoming",
        "existing_event_id": int(existing_event_id),
        "candidate_event_id": int(candidate_event_id) if candidate_event_id is not None else None,
        "candidate_vec": new_vec,
        "cosine": float(cosine),
        "reason": reason,
        "ts": _utc_now_iso(),
    }
    client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    client.rpush(DEDUP_QUEUE_KEY, json.dumps(payload, separators=(",", ":")))


# ---------------------------------------------------------------------------
# Hot path: precheck_duplicate
# ---------------------------------------------------------------------------


def precheck_duplicate(vec) -> Optional[int]:
    """Synchronous duplicate gate run on every Tier 2/3 embedding write.

    Returns:
        - existing event_id when cosine >= ``DEDUP_AUTO_MERGE`` (auto-merge);
        - ``None`` and pushes to ``cfp:dedup_pending`` when
          ``DEDUP_COSINE`` <= cosine < ``DEDUP_AUTO_MERGE``;
        - ``None`` silently when no neighbour passes the lower threshold.

    Skips rows where ``superseded_by IS NOT NULL`` -- merge losers must
    never come back as duplicate winners.
    """
    v = _check_dim(vec)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_id, 1 - (ee.vec <=> %s::vector) AS cosine
            FROM event_embeddings ee
            JOIN events e ON e.event_id = ee.event_id
            WHERE e.superseded_by IS NULL
            ORDER BY ee.vec <=> %s::vector
            LIMIT 1
            """,
            (v, v),
        )
        row = cur.fetchone()
    if row is None:
        return None
    cosine = float(row["cosine"])
    existing_id = int(row["event_id"])
    if cosine >= DEDUP_AUTO_MERGE:
        return existing_id
    if cosine >= DEDUP_COSINE:
        _push_pending(
            existing_event_id=existing_id,
            cosine=cosine,
            new_vec=v,
            reason="ann_topk_pending",
        )
    return None


async def find_duplicate(event) -> Optional[int]:
    """Async wrapper over ``precheck_duplicate`` for Tier 2 callers.

    Accepts either a list[float] vector or an object with a ``.vec``
    attribute. v1 keeps this synchronous internally -- pgvector lookups are
    fast enough that an extra thread-pool hop would only add latency.
    """
    vec = getattr(event, "vec", event)
    return precheck_duplicate(vec)


# ---------------------------------------------------------------------------
# Find candidates (top-K)
# ---------------------------------------------------------------------------


def find_candidates(
    vec,
    *,
    top_k: int = DEDUP_TOP_K,
    exclude_event_id: Optional[int] = None,
) -> list[Candidate]:
    """Top-K nearest neighbours with cosine >= ``DEDUP_COSINE``.

    Excludes superseded rows and (optionally) ``exclude_event_id`` so the
    caller can ask for "neighbours of event X other than X itself".
    """
    v = _check_dim(vec)
    extra_where = ""
    if exclude_event_id is not None:
        extra_where = "AND e.event_id <> %s"

    sql = f"""
        SELECT e.event_id, e.acronym, e.edition_year,
               1 - (ee.vec <=> %s::vector) AS cosine
        FROM event_embeddings ee
        JOIN events e ON e.event_id = ee.event_id
        WHERE e.superseded_by IS NULL
          AND 1 - (ee.vec <=> %s::vector) >= %s
          {extra_where}
        ORDER BY ee.vec <=> %s::vector
        LIMIT %s
    """
    # Reorder params to match placeholder order: vec, vec, min_cosine, [exclude], vec, limit.
    bind = [v, v, DEDUP_COSINE]
    if exclude_event_id is not None:
        bind.append(int(exclude_event_id))
    bind.extend([v, int(top_k)])

    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, bind)
        rows = cur.fetchall()

    out: list[Candidate] = []
    for r in rows:
        cosine = float(r["cosine"])
        reason = "ann_top1" if not out else "ann_topk"
        out.append(
            Candidate(
                candidate_id=int(r["event_id"]),
                cosine=cosine,
                acronym=r["acronym"],
                year=r["edition_year"],
                # v1: confidence is just the cosine; v2 may blend in tier_runs.
                confidence=cosine,
                reason=reason,
            )
        )
    return out


def _find_candidates_for_event(event_id: int, *, top_k: int) -> list[Candidate]:
    """Helper for ``sweep``: pull the row's vector, then ANN-search."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT vec FROM event_embeddings WHERE event_id = %s",
            (event_id,),
        )
        row = cur.fetchone()
    if row is None:
        return []
    raw = row["vec"]
    # pgvector returns either a string "[...]" or a list depending on adapter.
    if isinstance(raw, str):
        vec = [float(x) for x in raw.strip("[]").split(",") if x]
    else:
        vec = list(raw)
    return find_candidates(vec, top_k=top_k, exclude_event_id=event_id)


# ---------------------------------------------------------------------------
# merge_events -- column-by-column COALESCE
# ---------------------------------------------------------------------------

# Columns we COALESCE-merge from loser into winner. Excluded:
# event_id (PK), scraped_at (winner's stays), last_checked (NOW()),
# superseded_by (audit), notes (concatenated), scrape_session_id, raw_cfp_text.
_MERGEABLE_COLS = (
    "acronym", "name", "series_id", "edition_year", "categories",
    "is_workshop", "is_virtual", "when_raw", "start_date", "end_date",
    "abstract_deadline", "paper_deadline", "notification", "camera_ready",
    "where_raw", "country", "region", "india_state", "venue_id",
    "origin_url", "official_url", "submission_system", "sponsor_names",
    "raw_tags", "description", "rank", "source",
    "quality_flags", "quality_severity",
)


def merge_events(winner_id: int, loser_id: int, *, reason: str) -> None:
    """Merge ``loser_id`` into ``winner_id`` -- single transaction.

    Steps (in order):
      1. Lock both rows ``FOR UPDATE`` and validate state.
      2. COALESCE non-null fields from loser into winner; concat ``notes``.
      3. Reroute ``event_people`` / ``event_organisations`` / ``tier_runs``
         child FKs to the winner (PK-collision safe via ``NOT EXISTS``).
      4. Drop the loser's embedding.
      5. Mark loser ``superseded_by = winner`` and append an audit note.

    Raises ``ValueError`` if winner == loser, winner is itself superseded,
    or loser is already superseded -- merging twice would lose history.
    """
    if winner_id == loser_id:
        raise ValueError("winner_id == loser_id")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT event_id, superseded_by FROM events
                WHERE event_id IN (%s, %s) FOR UPDATE
                """,
                (winner_id, loser_id),
            )
            rows = {r["event_id"]: r for r in cur.fetchall()}
            if winner_id not in rows or loser_id not in rows:
                raise ValueError(
                    f"winner {winner_id} or loser {loser_id} not found"
                )
            if rows[winner_id]["superseded_by"] is not None:
                raise ValueError(f"winner {winner_id} is itself superseded")
            if rows[loser_id]["superseded_by"] is not None:
                raise ValueError(f"loser {loser_id} already superseded")

            # 1. COALESCE-merge non-null loser fields into winner.
            set_clause = ", ".join(
                f"{c} = COALESCE(w.{c}, l.{c})" for c in _MERGEABLE_COLS
            )
            cur.execute(
                f"""
                UPDATE events w SET {set_clause},
                    notes = CASE
                        WHEN COALESCE(l.notes, '') = '' THEN w.notes
                        ELSE COALESCE(w.notes, '')
                             || E'\\n--- merged from '
                             || l.event_id::text
                             || E' ---\\n'
                             || l.notes
                    END,
                    last_checked = NOW()
                FROM events l
                WHERE w.event_id = %s AND l.event_id = %s
                """,
                (winner_id, loser_id),
            )

            # 2. Reroute child FKs (PK-collision safe).
            for tbl, extra_pk in (
                ("event_people", "person_id"),
                ("event_organisations", "org_id"),
            ):
                cur.execute(
                    f"""
                    UPDATE {tbl} ep SET event_id = %s
                    WHERE ep.event_id = %s AND NOT EXISTS (
                        SELECT 1 FROM {tbl} ep2
                        WHERE ep2.event_id = %s
                          AND ep2.{extra_pk} IS NOT DISTINCT FROM ep.{extra_pk}
                          AND ep2.role = ep.role
                    )
                    """,
                    (winner_id, loser_id, winner_id),
                )
                cur.execute(
                    f"DELETE FROM {tbl} WHERE event_id = %s", (loser_id,)
                )

            cur.execute(
                "UPDATE tier_runs SET event_id = %s WHERE event_id = %s",
                (winner_id, loser_id),
            )

            # 3. Drop loser embedding -- prevents the loser from coming back
            # as an ANN candidate even before superseded_by is read.
            cur.execute(
                "DELETE FROM event_embeddings WHERE event_id = %s",
                (loser_id,),
            )

            # 4. Mark loser superseded with an audit note.
            cur.execute(
                """
                UPDATE events SET superseded_by = %s,
                    notes = COALESCE(notes, '')
                            || E'\\n[superseded by '
                            || %s::text
                            || ': '
                            || %s
                            || ']'
                WHERE event_id = %s
                """,
                (winner_id, winner_id, reason, loser_id),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# Acronym blocking (cross-source sanity net)
# ---------------------------------------------------------------------------


def acronym_blocking() -> list[tuple[int, int]]:
    """Return all (a, b) pairs (a < b) sharing normalised acronym + year.

    Uses ``GROUP BY (normalise(acronym), edition_year) HAVING count >= 2``
    semantics in Python (since ``_normalise_acronym`` is a Python regex,
    not a SQL function). For each group of size n, expand to all C(n, 2)
    pairs.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_id, acronym, edition_year FROM events
            WHERE superseded_by IS NULL
              AND acronym IS NOT NULL
              AND edition_year IS NOT NULL
            """
        )
        rows = cur.fetchall()

    by_key: dict[tuple[str, int], list[int]] = {}
    for r in rows:
        norm = _normalise_acronym(r["acronym"])
        if not norm:
            continue
        key = (norm, int(r["edition_year"]))
        by_key.setdefault(key, []).append(int(r["event_id"]))

    pairs: list[tuple[int, int]] = []
    for ids in by_key.values():
        if len(ids) < 2:
            continue
        ids_sorted = sorted(ids)
        for i in range(len(ids_sorted)):
            for j in range(i + 1, len(ids_sorted)):
                pairs.append((ids_sorted[i], ids_sorted[j]))
    return pairs


# ---------------------------------------------------------------------------
# sweep -- nightly job
# ---------------------------------------------------------------------------


def _fetch_cosine(a: int, b: int) -> Optional[float]:
    """Compute cosine similarity between two events' embeddings."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 - (a.vec <=> b.vec) AS cosine
            FROM event_embeddings a, event_embeddings b
            WHERE a.event_id = %s AND b.event_id = %s
            """,
            (a, b),
        )
        row = cur.fetchone()
    return float(row["cosine"]) if row else None


def _pick_winner(conn, a: int, b: int) -> tuple[int, int]:
    """Choose merge winner: highest tier confidence, tiebreak on scraped_at."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_id,
                   COALESCE(MAX(tr.confidence), 0.0) AS confidence,
                   e.scraped_at
            FROM events e
            LEFT JOIN tier_runs tr ON tr.event_id = e.event_id
            WHERE e.event_id = ANY(%s)
            GROUP BY e.event_id, e.scraped_at
            """,
            ([a, b],),
        )
        rows = cur.fetchall()
    rows.sort(
        key=lambda r: (
            -float(r["confidence"]),
            -(r["scraped_at"].timestamp() if r["scraped_at"] else 0.0),
        )
    )
    return int(rows[0]["event_id"]), int(rows[1]["event_id"])


def sweep() -> SweepReport:
    """Nightly cross-source sweep.

    1. ANN top-K for every live event -- collect (lo, hi) pairs above
       ``DEDUP_COSINE``.
    2. Acronym-year blocking for the cheap-but-noisy cross-source net.
    3. For each unique pair: auto-merge if cosine >= DEDUP_AUTO_MERGE,
       otherwise queue for v2 review. ``merged`` set prevents one event
       from being merged twice in the same pass.
    """
    started = time.monotonic()
    report = SweepReport(0, 0, 0, 0.0)

    # 1. ANN-driven pairs.
    ann_pairs: set[tuple[int, int]] = set()
    cosine_for: dict[tuple[int, int], float] = {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_id FROM events e
            JOIN event_embeddings ee ON ee.event_id = e.event_id
            WHERE e.superseded_by IS NULL
            """
        )
        all_ids = [int(r["event_id"]) for r in cur.fetchall()]

    for eid in all_ids:
        for c in _find_candidates_for_event(eid, top_k=DEDUP_TOP_K):
            lo, hi = sorted([eid, c.candidate_id])
            ann_pairs.add((lo, hi))
            cosine_for[(lo, hi)] = c.cosine

    # 2. Acronym-block pairs (cheap cross-source net).
    acr_pairs = set(acronym_blocking())
    all_pairs = ann_pairs | acr_pairs
    report.pairs_examined = len(all_pairs)

    # 3. Decide per pair (deterministic order for reproducibility).
    merged: set[int] = set()
    for (a, b) in sorted(all_pairs):
        if a in merged or b in merged:
            continue
        cos = cosine_for.get((a, b))
        if cos is None:
            cos = _fetch_cosine(a, b)
        if cos is not None and cos >= DEDUP_AUTO_MERGE:
            with get_conn() as conn:
                w, l = _pick_winner(conn, a, b)
            try:
                merge_events(w, l, reason=f"sweep_auto cosine={cos:.3f}")
                merged.add(l)
                report.merges_applied += 1
            except ValueError:
                # Either side was already merged in this pass via another
                # transitive pair -- safe to skip.
                continue
        elif (cos is not None and cos >= DEDUP_COSINE) or (a, b) in acr_pairs:
            _push_pending(
                existing_event_id=a,
                candidate_event_id=b,
                cosine=cos if cos is not None else 0.0,
                reason="sweep" if cos is not None else "acronym_match",
            )
            report.pairs_queued_for_llm += 1

    report.duration_seconds = time.monotonic() - started
    return report
