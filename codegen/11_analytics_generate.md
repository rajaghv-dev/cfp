# Codegen 11 — wcfp/analytics.py + generate_md.py (replacement)

## Files to Create/Replace
- `wcfp/analytics.py` (new)
- `generate_md.py` (REPLACE — same filename, new implementation)

## Key rule
generate_md.py currently reads `data/latest.json`. Replace that with DuckDB→PostgreSQL.
ALL of the location taxonomy, India state-wise logic, table helpers MUST be preserved.

---

## wcfp/analytics.py — full implementation

```python
"""DuckDB analytics layer. Reads PostgreSQL via postgres_scanner. Never writes."""
import duckdb
from config import PG_DSN

def get_analytics_conn() -> duckdb.DuckDBPyConnection:
    """Return an in-memory DuckDB connection attached to PostgreSQL as 'pg'."""
    conn = duckdb.connect()
    conn.execute("INSTALL postgres; LOAD postgres;")
    conn.execute(f"ATTACH '{PG_DSN}' AS pg (TYPE POSTGRES, READ_ONLY)")
    return conn

def export_parquet(output_path: str) -> None:
    """Snapshot all events to a Parquet file for archiving."""
    conn = get_analytics_conn()
    conn.execute(f"""
        COPY (SELECT * FROM pg.events ORDER BY start_date)
        TO '{output_path}' (FORMAT PARQUET)
    """)

def query_df(sql: str):
    """Run a SQL query against the pg-attached DuckDB; return a pandas DataFrame."""
    import pandas as pd
    conn = get_analytics_conn()
    return conn.execute(sql).df()
```

---

## generate_md.py — what to change vs. what to keep

### KEEP VERBATIM (copy from current generate_md.py)
All of the following must be preserved exactly:

```
_UK list
_CH list
_EU_EXCL list
_USA list
_ONLINE list
_SG list
_INDIA_RE regex
CITY_STATE dict (80+ city→state mappings)
location_tags(where: str) -> set[str]
india_state(where: str) -> str
_header(with_cat: bool) -> str
_row(c: dict, with_cat: bool) -> str
_sort_up(confs) -> list
_sort_past(confs) -> list
_table_section(...)
_write(path, lines)
_meta(confs) -> str
write_category_md(path, title, confs)
write_by_date_md(path, confs)
write_region_md(path, title, confs)
write_india_md(path, confs)
CAT_TITLES dict
```

### CHANGE: data loading

Replace this:
```python
# OLD — remove this
all_confs = json.loads(DATA_FILE.read_text(encoding="utf-8"))
```

With this:
```python
# NEW — load from DuckDB→PostgreSQL
from wcfp.analytics import get_analytics_conn

def load_all_events() -> list[dict]:
    conn = get_analytics_conn()
    rows = conn.execute("""
        SELECT
            event_id, acronym, name,
            array_to_string(categories, ',') AS category,
            is_virtual,
            when_raw,
            start_date::VARCHAR AS start_date,
            end_date::VARCHAR AS end_date,
            where_raw,
            country, region, india_state,
            deadline::VARCHAR AS deadline,
            notification::VARCHAR AS notification,
            wikicfp_url, official_url,
            raw_tags, description, source
        FROM pg.events
    """).fetchdf().to_dict('records')
    # Normalise: generate_md.py expects 'when' and 'where' keys
    for r in rows:
        r["when"]  = r.pop("when_raw", "") or r.get("start_date", "") or ""
        r["where"] = r.pop("where_raw", "") or ""
        r["url"]   = r.get("official_url") or r.get("wikicfp_url") or ""
        # keywords field expected by old code
        r["keywords"] = []
    return rows
```

### CHANGE: generate_all() signature

```python
def generate_all(data_file=None, reports_dir=REPORTS_DIR) -> None:
    """
    Generate all Markdown reports.
    data_file is ignored (kept for backwards compat); data is loaded from PostgreSQL.
    Falls back to data/latest.json if PostgreSQL is unavailable.
    """
    try:
        all_confs = load_all_events()
        print(f"Loaded {len(all_confs)} events from PostgreSQL")
    except Exception as e:
        print(f"[warn] PostgreSQL unavailable ({e}), falling back to latest.json")
        import json
        from pathlib import Path
        fallback = Path("data/latest.json")
        if fallback.exists():
            all_confs = json.loads(fallback.read_text())
            print(f"Loaded {len(all_confs)} events from latest.json")
        else:
            print("No data source available")
            return
    # ... rest of generate_all unchanged
```

### ADD: notes preservation pattern (from conf-scr-org-syn)

When writing per-conference note files in `notes/<category>/<SLUG>.md`,
preserve any user content under the `## Notes` section:

```python
NOTES_DIR = Path("notes")

def write_event_note(event_dict: dict, overwrite_notes: bool = False) -> None:
    """Write a per-conference Markdown note, preserving user's ## Notes content."""
    from wcfp.models import Event
    # Derive category subdir and slug
    cat  = (event_dict.get("category") or "general").lower().replace(",", "_").split("_")[0]
    slug = _derive_slug(event_dict)  # ACRONYM-YEAR
    path = NOTES_DIR / cat / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    existing_notes = ""
    if path.exists() and not overwrite_notes:
        existing = path.read_text(encoding="utf-8")
        marker = "\n## Notes\n"
        if marker in existing:
            existing_notes = existing.split(marker, 1)[1].strip()

    # Build markdown
    lines = [... ]   # use existing _row / _meta helpers for the table sections

    if existing_notes:
        lines += ["", "## Notes", "", existing_notes]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
```

---

## CAT_TITLES — must include all 13 Category enum values

```python
CAT_TITLES: dict[str, str] = {
    "AI":              "AI — Artificial Intelligence",
    "ML":              "ML — Machine Learning & Deep Learning",
    "DevOps":          "DevOps & Site Reliability Engineering",
    "Linux":           "Linux & Open Source",
    "ChipDesign":      "Chip Design — VLSI / EDA / FPGA / Semiconductor",
    "Math":            "Mathematics",
    "Legal":           "Legal, Cyber Law & Intellectual Property",
    "ComputerScience": "Computer Science (General)",
    "Security":        "Security & Privacy",
    "Data":            "Databases & Data Engineering",
    "Networking":      "Networking & Communications",
    "Robotics":        "Robotics & Automation",
    "Bioinformatics":  "Bioinformatics & Healthcare IT",
}
```
