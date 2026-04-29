"""Analytics layer. v1: reads from PostgreSQL via psycopg3, hands off to
the existing generate_md.py for Markdown emission. v2 will introduce DuckDB
postgres_scanner for heavier analytical queries."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from config import PG_DSN, REPORTS_DIR


def _row_to_legacy_dict(row: dict[str, Any]) -> dict[str, Any]:
    """Project a `events` row into the dict shape that generate_md.py expects.
    Why: generate_md.py was written against data/latest.json which uses
    snake_case keys; matching them here means we can re-use its formatting
    helpers verbatim (location taxonomy, India state-wise rules, etc.)."""
    return {
        "acronym":        row.get("acronym"),
        "name":           row.get("name"),
        "category":       (row.get("categories") or [None])[0]
                          if isinstance(row.get("categories"), list)
                          else row.get("categories"),
        "categories":     row.get("categories") or [],
        "rank":           row.get("rank"),
        "where":          row.get("where_raw") or "",
        "when":           row.get("when_raw") or "",
        "deadline":       row["paper_deadline"].isoformat()
                          if row.get("paper_deadline") else "",
        "abstract_deadline": row["abstract_deadline"].isoformat()
                          if row.get("abstract_deadline") else "",
        "notification":   row["notification"].isoformat()
                          if row.get("notification") else "",
        "url":            row.get("official_url") or row.get("origin_url") or "",
        "description":    row.get("description") or "",
        "raw_tags":       row.get("raw_tags") or [],
        "is_workshop":    row.get("is_workshop", False),
        "country":        row.get("country"),
    }


def load_events_from_pg() -> list[dict[str, Any]]:
    """Read all unsuperseded events from PostgreSQL and return them as a list
    of dicts in the legacy generate_md.py format."""
    with psycopg.connect(PG_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM events
                WHERE superseded_by IS NULL
                ORDER BY paper_deadline NULLS LAST, start_date NULLS LAST, acronym
            """)
            rows = cur.fetchall()
    return [_row_to_legacy_dict(r) for r in rows]


def generate_all(reports_dir: Path | None = None) -> int:
    """Emit all 13 Markdown reports from current PG state. Returns row count.
    Uses generate_md.py's existing helpers (location taxonomy, India state
    logic, deadline coloring) — we only swap the data loader."""
    import generate_md

    out_dir = reports_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    confs = load_events_from_pg()

    # Reuse generate_md's category/region/date dispatchers
    if hasattr(generate_md, "generate_all"):
        generate_md.generate_all(confs, out_dir=out_dir)
    else:
        # Fallback: manually invoke individual writers using its public helpers
        if hasattr(generate_md, "write_by_date_md"):
            generate_md.write_by_date_md(out_dir / "by_date.md", confs)
        for cat_name, fname in (("AI", "ai.md"), ("ML", "ml.md"),
                                ("DevOps", "devops.md"), ("Linux", "linux.md"),
                                ("ChipDesign", "chipdesign.md"),
                                ("Math", "math.md"), ("Legal", "legal.md")):
            cat_confs = [c for c in confs
                         if cat_name in (c.get("categories") or [])]
            if hasattr(generate_md, "write_category_md"):
                generate_md.write_category_md(out_dir / fname, cat_name, cat_confs)

    return len(confs)


# DuckDB-attached analytics (v2-leaning; available for use today)

def get_analytics_conn():
    """Return a DuckDB connection with `pg` attached read-only."""
    import duckdb
    conn = duckdb.connect()
    conn.execute("INSTALL postgres; LOAD postgres;")
    conn.execute(f"ATTACH '{PG_DSN}' AS pg (TYPE POSTGRES, READ_ONLY)")
    return conn


def query_df(sql: str):
    """Run SQL against the pg-attached DuckDB; return pandas DataFrame.
    Lazy-imports pandas so it's not a hard dependency for the report path."""
    return get_analytics_conn().execute(sql).df()


def export_parquet(output_path: Path | str) -> None:
    """Snapshot events to Parquet (for v2 archive use)."""
    conn = get_analytics_conn()
    conn.execute(f"""
        COPY (SELECT * FROM pg.events WHERE superseded_by IS NULL
              ORDER BY start_date NULLS LAST)
        TO '{output_path}' (FORMAT PARQUET)
    """)
