"""PostgreSQL access layer for the CFP pipeline.

All writes go through this module. psycopg3 only — never psycopg2.
DuckDB is read-only analytics, Redis is operational queue; neither lives here.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from dateutil import parser as dateparser

from config import PG_DSN
from cfp.models import Category, Event, Organisation, Person, Series, Venue


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_conn() -> psycopg.Connection:
    """Return a psycopg3 connection with dict_row factory.

    Caller is responsible for using as a context manager (``with get_conn() as
    conn``) so the connection is closed and the transaction committed/rolled
    back deterministically.
    """
    return psycopg.connect(PG_DSN, row_factory=dict_row)


# ---------------------------------------------------------------------------
# Schema (codegen/05 + codegen/15 superseded_by)
# ---------------------------------------------------------------------------

_DDL_EXTENSIONS = "CREATE EXTENSION IF NOT EXISTS vector;"

_DDL_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS organisations (
        org_id   SERIAL PRIMARY KEY,
        name     VARCHAR NOT NULL,
        type     VARCHAR,
        country  VARCHAR,
        website  VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS series (
        series_id   INTEGER PRIMARY KEY,
        acronym     VARCHAR NOT NULL,
        full_name   VARCHAR,
        origin_url  VARCHAR,
        org_id      INTEGER REFERENCES organisations(org_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS venues (
        venue_id  SERIAL PRIMARY KEY,
        name      VARCHAR,
        city      VARCHAR,
        state     VARCHAR,
        country   VARCHAR,
        latitude  DOUBLE PRECISION,
        longitude DOUBLE PRECISION
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS people (
        person_id SERIAL PRIMARY KEY,
        full_name VARCHAR NOT NULL,
        email     VARCHAR,
        dblp_url  VARCHAR,
        homepage  VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scrape_sessions (
        session_id     VARCHAR PRIMARY KEY,
        started_at     TIMESTAMPTZ DEFAULT NOW(),
        finished_at    TIMESTAMPTZ,
        machine        VARCHAR,
        git_sha        VARCHAR,
        prompts_md_sha VARCHAR,
        notes          TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id           SERIAL PRIMARY KEY,
        acronym            VARCHAR,
        name               VARCHAR,
        series_id          INTEGER REFERENCES series(series_id),
        edition_year       INTEGER,
        categories         VARCHAR[],
        is_workshop        BOOLEAN DEFAULT FALSE,
        is_virtual         BOOLEAN DEFAULT FALSE,
        when_raw           VARCHAR,
        start_date         DATE,
        end_date           DATE,
        abstract_deadline  DATE,
        paper_deadline     DATE,
        notification       DATE,
        camera_ready       DATE,
        where_raw          VARCHAR,
        country            VARCHAR,
        region             VARCHAR,
        india_state        VARCHAR,
        venue_id           INTEGER REFERENCES venues(venue_id),
        origin_url         VARCHAR,
        official_url       VARCHAR,
        submission_system  VARCHAR,
        sponsor_names      VARCHAR[],
        raw_tags           VARCHAR[],
        raw_cfp_text       TEXT,
        description        VARCHAR,
        notes              TEXT DEFAULT '',
        rank               VARCHAR,
        source             VARCHAR DEFAULT 'wikicfp',
        quality_flags      VARCHAR[],
        quality_severity   VARCHAR,
        scrape_session_id  VARCHAR REFERENCES scrape_sessions(session_id),
        scraped_at         TIMESTAMPTZ DEFAULT NOW(),
        last_checked       TIMESTAMPTZ DEFAULT NOW(),
        superseded_by      INTEGER REFERENCES events(event_id)
    )
    """,
    # codegen/15: ensure superseded_by exists on a pre-existing schema
    "ALTER TABLE events ADD COLUMN IF NOT EXISTS superseded_by INTEGER REFERENCES events(event_id)",
    """
    CREATE TABLE IF NOT EXISTS event_people (
        event_id  INTEGER REFERENCES events(event_id),
        person_id INTEGER REFERENCES people(person_id),
        role      VARCHAR,
        PRIMARY KEY (event_id, person_id, role)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS person_affiliations (
        person_id INTEGER REFERENCES people(person_id),
        org_id    INTEGER REFERENCES organisations(org_id),
        year      INTEGER,
        role      VARCHAR,
        PRIMARY KEY (person_id, org_id, year)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_organisations (
        event_id INTEGER REFERENCES events(event_id),
        org_id   INTEGER REFERENCES organisations(org_id),
        role     VARCHAR,
        PRIMARY KEY (event_id, org_id, role)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_embeddings (
        event_id  INTEGER PRIMARY KEY REFERENCES events(event_id),
        vec       vector(768),
        text_hash VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS concept_embeddings (
        concept_name VARCHAR PRIMARY KEY,
        vec          vector(768)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sites (
        domain        VARCHAR PRIMARY KEY,
        parser_type   VARCHAR,
        last_scraped  TIMESTAMPTZ,
        robots_txt    VARCHAR,
        crawl_delay_s INTEGER DEFAULT 5,
        last_cursor   VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tier_runs (
        event_id        INTEGER REFERENCES events(event_id),
        tier            INTEGER,
        model           VARCHAR,
        confidence      DOUBLE PRECISION,
        output_json     JSONB,
        escalate        BOOLEAN,
        escalate_reason VARCHAR,
        elapsed_ms      INTEGER,
        ts              TIMESTAMPTZ DEFAULT NOW(),
        scrape_session_id VARCHAR
    )
    """,
    # event_id may be NULL when Tier 1 discards a non-CFP page; PRIMARY KEY on
    # nullable columns is rejected by Postgres so use a non-null surrogate.
    """
    CREATE TABLE IF NOT EXISTS scrape_queue (
        url             VARCHAR PRIMARY KEY,
        domain          VARCHAR,
        priority        INTEGER,
        source_event_id INTEGER,
        attempts        INTEGER DEFAULT 0,
        status          VARCHAR DEFAULT 'pending',
        added_at        TIMESTAMPTZ DEFAULT NOW(),
        last_error      VARCHAR
    )
    """,
]

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_country        ON events(country)",
    "CREATE INDEX IF NOT EXISTS idx_events_state          ON events(india_state)",
    "CREATE INDEX IF NOT EXISTS idx_events_start          ON events(start_date)",
    "CREATE INDEX IF NOT EXISTS idx_events_paper_deadline ON events(paper_deadline)",
    "CREATE INDEX IF NOT EXISTS idx_events_categories     ON events USING GIN(categories)",
    "CREATE INDEX IF NOT EXISTS idx_events_tags           ON events USING GIN(raw_tags)",
    "CREATE INDEX IF NOT EXISTS idx_events_sponsors       ON events USING GIN(sponsor_names)",
    "CREATE INDEX IF NOT EXISTS idx_events_session        ON events(scrape_session_id)",
    "CREATE INDEX IF NOT EXISTS idx_events_superseded_by  ON events(superseded_by)",
    "CREATE INDEX IF NOT EXISTS idx_people_name           ON people(full_name)",
    "CREATE INDEX IF NOT EXISTS idx_venues_location       ON venues(country, city)",
    "CREATE INDEX IF NOT EXISTS idx_tier_runs_event       ON tier_runs(event_id)",
]


def init_db() -> None:
    """Create all tables, indexes and required extensions. Idempotent.

    v1 only: no Apache AGE setup (the pgvector/pgvector:pg16 image lacks it).
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL_EXTENSIONS)
            for stmt in _DDL_TABLES:
                cur.execute(stmt)
            for stmt in _DDL_INDEXES:
                cur.execute(stmt)
        conn.commit()


# ---------------------------------------------------------------------------
# Event upsert (COALESCE policy — codegen/05 §upsert_event)
# ---------------------------------------------------------------------------

def event_to_dict(event: Event) -> dict:
    """Serialise an Event dataclass for psycopg named-parameter binding."""
    d = asdict(event)
    d["categories"] = [c.value if isinstance(c, Category) else str(c)
                       for c in event.categories]
    return d


def upsert_event(conn: psycopg.Connection, event: Event) -> None:
    """INSERT ... ON CONFLICT (event_id) DO UPDATE with COALESCE protection.

    Preserved (COALESCE old over new-null):
        notification, camera_ready, rank, notes, submission_system,
        sponsor_names, official_url, description.
    Always overwritten:
        paper_deadline, abstract_deadline, dates, location, quality_flags,
        quality_severity, scrape_session_id, raw_tags, last_checked.
    """
    sql = """
        INSERT INTO events (
            event_id, acronym, name, series_id, edition_year,
            categories, is_workshop, is_virtual, when_raw,
            start_date, end_date, abstract_deadline, paper_deadline,
            notification, camera_ready,
            where_raw, country, region, india_state,
            origin_url, official_url, submission_system, sponsor_names,
            raw_tags, description, source,
            quality_flags, quality_severity, scrape_session_id,
            rank, notes,
            scraped_at, last_checked
        ) VALUES (
            %(event_id)s, %(acronym)s, %(name)s, %(series_id)s, %(edition_year)s,
            %(categories)s, %(is_workshop)s, %(is_virtual)s, %(when_raw)s,
            %(start_date)s, %(end_date)s, %(abstract_deadline)s, %(paper_deadline)s,
            %(notification)s, %(camera_ready)s,
            %(where_raw)s, %(country)s, %(region)s, %(india_state)s,
            %(origin_url)s, %(official_url)s, %(submission_system)s, %(sponsor_names)s,
            %(raw_tags)s, %(description)s, %(source)s,
            %(quality_flags)s, %(quality_severity)s, %(scrape_session_id)s,
            %(rank)s, %(notes)s,
            NOW(), NOW()
        )
        ON CONFLICT (event_id) DO UPDATE SET
            acronym           = EXCLUDED.acronym,
            name              = EXCLUDED.name,
            edition_year      = EXCLUDED.edition_year,
            categories        = EXCLUDED.categories,
            is_workshop       = EXCLUDED.is_workshop,
            is_virtual        = EXCLUDED.is_virtual,
            when_raw          = EXCLUDED.when_raw,
            start_date        = EXCLUDED.start_date,
            end_date          = EXCLUDED.end_date,
            abstract_deadline = EXCLUDED.abstract_deadline,
            paper_deadline    = EXCLUDED.paper_deadline,
            where_raw         = EXCLUDED.where_raw,
            country           = EXCLUDED.country,
            region            = EXCLUDED.region,
            india_state       = EXCLUDED.india_state,
            origin_url        = EXCLUDED.origin_url,
            official_url      = COALESCE(EXCLUDED.official_url,      events.official_url),
            notification      = COALESCE(EXCLUDED.notification,      events.notification),
            camera_ready      = COALESCE(EXCLUDED.camera_ready,      events.camera_ready),
            rank              = COALESCE(EXCLUDED.rank,              events.rank),
            description       = COALESCE(EXCLUDED.description,       events.description),
            submission_system = COALESCE(EXCLUDED.submission_system, events.submission_system),
            sponsor_names     = COALESCE(EXCLUDED.sponsor_names,     events.sponsor_names),
            notes             = COALESCE(NULLIF(events.notes, ''),   NULLIF(EXCLUDED.notes, ''), ''),
            raw_tags          = EXCLUDED.raw_tags,
            quality_flags     = EXCLUDED.quality_flags,
            quality_severity  = EXCLUDED.quality_severity,
            scrape_session_id = EXCLUDED.scrape_session_id,
            last_checked      = NOW()
    """
    with conn.cursor() as cur:
        cur.execute(sql, event_to_dict(event))
    conn.commit()


def get_event_by_id(conn: psycopg.Connection, event_id: int) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM events WHERE event_id = %s", (event_id,))
        return cur.fetchone()


# ---------------------------------------------------------------------------
# Person / Venue / Organisation / Series upserts
# ---------------------------------------------------------------------------

def upsert_person(conn: psycopg.Connection, person: Person) -> int:
    """INSERT or UPDATE a person, returning the (possibly-new) person_id.

    A Person dataclass with person_id <= 0 is treated as a fresh insert; the
    SERIAL column assigns a real id. Re-using an existing person_id triggers
    an UPDATE on that row.
    """
    with conn.cursor() as cur:
        if person.person_id and person.person_id > 0:
            cur.execute(
                """
                INSERT INTO people (person_id, full_name, email, dblp_url, homepage)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (person_id) DO UPDATE SET
                    full_name = EXCLUDED.full_name,
                    email     = COALESCE(EXCLUDED.email,    people.email),
                    dblp_url  = COALESCE(EXCLUDED.dblp_url, people.dblp_url),
                    homepage  = COALESCE(EXCLUDED.homepage, people.homepage)
                RETURNING person_id
                """,
                (person.person_id, person.full_name, person.email,
                 person.dblp_url, person.homepage),
            )
        else:
            cur.execute(
                """
                INSERT INTO people (full_name, email, dblp_url, homepage)
                VALUES (%s, %s, %s, %s)
                RETURNING person_id
                """,
                (person.full_name, person.email, person.dblp_url, person.homepage),
            )
        row = cur.fetchone()
    conn.commit()
    return int(row["person_id"])


def upsert_venue(conn: psycopg.Connection, venue: Venue) -> int:
    with conn.cursor() as cur:
        if venue.venue_id and venue.venue_id > 0:
            cur.execute(
                """
                INSERT INTO venues (venue_id, name, city, state, country, latitude, longitude)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (venue_id) DO UPDATE SET
                    name      = COALESCE(EXCLUDED.name,      venues.name),
                    city      = COALESCE(EXCLUDED.city,      venues.city),
                    state     = COALESCE(EXCLUDED.state,     venues.state),
                    country   = COALESCE(EXCLUDED.country,   venues.country),
                    latitude  = COALESCE(EXCLUDED.latitude,  venues.latitude),
                    longitude = COALESCE(EXCLUDED.longitude, venues.longitude)
                RETURNING venue_id
                """,
                (venue.venue_id, venue.name, venue.city, venue.state,
                 venue.country, venue.latitude, venue.longitude),
            )
        else:
            cur.execute(
                """
                INSERT INTO venues (name, city, state, country, latitude, longitude)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING venue_id
                """,
                (venue.name, venue.city, venue.state, venue.country,
                 venue.latitude, venue.longitude),
            )
        row = cur.fetchone()
    conn.commit()
    return int(row["venue_id"])


def upsert_org(conn: psycopg.Connection, org: Organisation) -> int:
    type_str = org.type.value if hasattr(org.type, "value") else str(org.type) if org.type else None
    with conn.cursor() as cur:
        if org.org_id and org.org_id > 0:
            cur.execute(
                """
                INSERT INTO organisations (org_id, name, type, country, website)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (org_id) DO UPDATE SET
                    name    = EXCLUDED.name,
                    type    = COALESCE(EXCLUDED.type,    organisations.type),
                    country = COALESCE(EXCLUDED.country, organisations.country),
                    website = COALESCE(EXCLUDED.website, organisations.website)
                RETURNING org_id
                """,
                (org.org_id, org.name, type_str, org.country, org.website),
            )
        else:
            cur.execute(
                """
                INSERT INTO organisations (name, type, country, website)
                VALUES (%s, %s, %s, %s)
                RETURNING org_id
                """,
                (org.name, type_str, org.country, org.website),
            )
        row = cur.fetchone()
    conn.commit()
    return int(row["org_id"])


def upsert_series(conn: psycopg.Connection, series: Series) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO series (series_id, acronym, full_name, origin_url, org_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (series_id) DO UPDATE SET
                acronym    = EXCLUDED.acronym,
                full_name  = COALESCE(EXCLUDED.full_name,  series.full_name),
                origin_url = COALESCE(EXCLUDED.origin_url, series.origin_url),
                org_id     = COALESCE(EXCLUDED.org_id,     series.org_id)
            """,
            (series.series_id, series.acronym, series.full_name,
             series.origin_url, series.org_id),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Link tables
# ---------------------------------------------------------------------------

def link_event_person(conn: psycopg.Connection, event_id: int,
                      person_id: int, role: str) -> None:
    role_str = role.value if hasattr(role, "value") else str(role)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO event_people (event_id, person_id, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (event_id, person_id, role) DO NOTHING
            """,
            (event_id, person_id, role_str),
        )
    conn.commit()


def link_event_org(conn: psycopg.Connection, event_id: int,
                   org_id: int, role: str) -> None:
    role_str = role.value if hasattr(role, "value") else str(role)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO event_organisations (event_id, org_id, role)
            VALUES (%s, %s, %s)
            ON CONFLICT (event_id, org_id, role) DO NOTHING
            """,
            (event_id, org_id, role_str),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Tier-1 minimal insert + tier_runs audit log + collapse_event (codegen/10, /15)
# ---------------------------------------------------------------------------

def insert_minimal_event(
    conn: psycopg.Connection,
    *,
    acronym: str,
    name: str,
    categories: list,
    is_workshop: bool,
    is_virtual: bool,
    origin_url: Optional[str],
    source: str,
    scrape_session_id: Optional[str],
) -> int:
    """Insert a placeholder Event row from Tier 1 output. Returns SERIAL event_id."""
    cat_values = [c.value if isinstance(c, Category) else str(c) for c in categories]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO events (
                acronym, name, categories, is_workshop, is_virtual,
                origin_url, source, scrape_session_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING event_id
            """,
            (acronym, name, cat_values, is_workshop, is_virtual,
             origin_url, source, scrape_session_id),
        )
        row = cur.fetchone()
    conn.commit()
    return int(row["event_id"])


def insert_tier_run(
    conn: psycopg.Connection,
    *,
    event_id: Optional[int],
    tier: int,
    model: str,
    confidence: float,
    output_json: Any,
    escalate: bool,
    escalate_reason: Optional[str],
    elapsed_ms: int,
    scrape_session_id: Optional[str] = None,
) -> None:
    """Append a row to tier_runs. event_id may be None when Tier 1 discards."""
    payload = output_json if isinstance(output_json, Jsonb) else Jsonb(output_json or {})
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tier_runs (
                event_id, tier, model, confidence, output_json,
                escalate, escalate_reason, elapsed_ms, scrape_session_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (event_id, tier, model, confidence, payload,
             escalate, escalate_reason, elapsed_ms, scrape_session_id),
        )
    conn.commit()


def collapse_event(conn: psycopg.Connection, *, src: int, dst: int) -> None:
    """Mark event ``src`` as superseded by ``dst``; reroute child FKs.

    Single transaction. Skips rows that would violate the destination's PK
    (event_people / event_organisations) — the duplicates are then deleted.
    """
    if src == dst:
        raise ValueError("src == dst")
    with conn.cursor() as cur:
        # event_people: move rows that have no clashing PK at dst, drop the rest
        cur.execute(
            """
            UPDATE event_people ep SET event_id = %s
            WHERE ep.event_id = %s AND NOT EXISTS (
                SELECT 1 FROM event_people ep2
                WHERE ep2.event_id = %s
                  AND ep2.person_id = ep.person_id
                  AND ep2.role = ep.role
            )
            """,
            (dst, src, dst),
        )
        cur.execute("DELETE FROM event_people WHERE event_id = %s", (src,))

        cur.execute(
            """
            UPDATE event_organisations eo SET event_id = %s
            WHERE eo.event_id = %s AND NOT EXISTS (
                SELECT 1 FROM event_organisations eo2
                WHERE eo2.event_id = %s
                  AND eo2.org_id = eo.org_id
                  AND eo2.role = eo.role
            )
            """,
            (dst, src, dst),
        )
        cur.execute("DELETE FROM event_organisations WHERE event_id = %s", (src,))

        cur.execute(
            "UPDATE tier_runs SET event_id = %s WHERE event_id = %s",
            (dst, src),
        )
        cur.execute(
            "DELETE FROM event_embeddings WHERE event_id = %s", (src,)
        )
        cur.execute(
            "UPDATE events SET superseded_by = %s WHERE event_id = %s",
            (dst, src),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Sessions (codegen/05 §S9)
# ---------------------------------------------------------------------------

def begin_session(
    conn: psycopg.Connection,
    *,
    machine: str,
    git_sha: str,
    prompts_md_sha: str,
) -> str:
    """Insert a row into scrape_sessions. session_id = ISO-Z + machine + uuid8."""
    started = datetime.now(timezone.utc)
    short_uuid = uuid.uuid4().hex[:8]
    session_id = f"{started.strftime('%Y-%m-%dT%H:%MZ')}-{machine}-{short_uuid}"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO scrape_sessions (session_id, started_at, machine,
                                         git_sha, prompts_md_sha)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (session_id, started, machine, git_sha, prompts_md_sha),
        )
    conn.commit()
    return session_id


def end_session(conn: psycopg.Connection, session_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE scrape_sessions SET finished_at = NOW() WHERE session_id = %s",
            (session_id,),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Seed loader (data/latest.json → events)
# ---------------------------------------------------------------------------

_DATE_RANGE_RE = re.compile(r"\s*[-–—]\s*")
_EVENT_ID_RE = re.compile(r"[?&]eventid=(\d+)")


def _parse_date(text: Optional[str]) -> Optional[date]:
    if not text:
        return None
    s = text.strip()
    if not s or s.lower() in {"n/a", "tba", "tbd"}:
        return None
    try:
        return dateparser.parse(s, default=datetime(2000, 1, 1)).date()
    except (ValueError, TypeError, OverflowError):
        return None


def _split_when(when: Optional[str]) -> tuple[Optional[date], Optional[date]]:
    if not when or when.strip().lower() in {"n/a", "tba", "tbd"}:
        return None, None
    parts = _DATE_RANGE_RE.split(when, maxsplit=1)
    if len(parts) == 2:
        return _parse_date(parts[0]), _parse_date(parts[1])
    d = _parse_date(when)
    return d, d


def _parse_categories(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    valid = {c.value.lower(): c.value for c in Category}
    out: list[str] = []
    for tok in re.split(r"[,/;]", raw):
        key = tok.strip().lower()
        if key in valid and valid[key] not in out:
            out.append(valid[key])
    return out


def _extract_event_id(url: Optional[str]) -> Optional[int]:
    if not url:
        return None
    m = _EVENT_ID_RE.search(url)
    return int(m.group(1)) if m else None


def _extract_year(acronym: Optional[str], name: Optional[str]) -> Optional[int]:
    for source in (acronym, name):
        if not source:
            continue
        m = re.search(r"\b(20\d{2})\b", source)
        if m:
            return int(m.group(1))
    return None


def seed_from_json(
    conn: psycopg.Connection,
    json_path: Path,
    scrape_session_id: Optional[str],
) -> int:
    """Load conferences from ``json_path`` (latest.json shape) into events.

    Existing rows are upserted via INSERT ... ON CONFLICT (event_id) DO UPDATE.
    Returns the number of records ingested. Records missing both an eventid in
    the URL and a usable acronym are skipped.
    """
    raw = json.loads(Path(json_path).read_text())
    if not isinstance(raw, list):
        raise ValueError("seed_from_json expects a top-level JSON array")

    inserted = 0
    with conn.cursor() as cur:
        for rec in raw:
            event_id = _extract_event_id(rec.get("url"))
            if event_id is None:
                continue
            acronym = (rec.get("acronym") or "").strip()
            name = (rec.get("name") or acronym).strip()
            categories = _parse_categories(rec.get("category"))
            start_date, end_date = _split_when(rec.get("when"))
            paper_deadline = _parse_date(rec.get("deadline"))
            cur.execute(
                """
                INSERT INTO events (
                    event_id, acronym, name, categories, when_raw,
                    start_date, end_date, paper_deadline,
                    where_raw, raw_tags, origin_url, source,
                    edition_year, scrape_session_id
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s
                )
                ON CONFLICT (event_id) DO UPDATE SET
                    acronym         = EXCLUDED.acronym,
                    name            = EXCLUDED.name,
                    categories      = EXCLUDED.categories,
                    when_raw        = EXCLUDED.when_raw,
                    start_date      = EXCLUDED.start_date,
                    end_date        = EXCLUDED.end_date,
                    paper_deadline  = EXCLUDED.paper_deadline,
                    where_raw       = EXCLUDED.where_raw,
                    raw_tags        = EXCLUDED.raw_tags,
                    origin_url      = EXCLUDED.origin_url,
                    edition_year    = EXCLUDED.edition_year,
                    scrape_session_id = EXCLUDED.scrape_session_id,
                    last_checked    = NOW()
                """,
                (
                    event_id, acronym, name, categories, rec.get("when"),
                    start_date, end_date, paper_deadline,
                    rec.get("where"), rec.get("keywords") or [],
                    rec.get("url"), "wikicfp",
                    _extract_year(acronym, name), scrape_session_id,
                ),
            )
            inserted += 1
    conn.commit()
    return inserted
