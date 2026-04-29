# Codegen 05 — cfp/db.py

## File to Create
- `cfp/db.py`

## Imports
```python
import psycopg          # psycopg3, NOT psycopg2
from psycopg.rows import dict_row
from config import PG_DSN, AGE_GRAPH
from cfp.models import Event, Person, Organisation, Venue, Series
```

---

## Connection helper

```python
def get_conn():
    return psycopg.connect(PG_DSN, row_factory=dict_row)
```

---

## init_db() — run once; idempotent (CREATE TABLE IF NOT EXISTS)

Create all tables in this exact order (FK deps):
1. organisations
2. series (FK → organisations)
3. venues
4. people
5. scrape_sessions   (audit trail — arch.md §4 S9)
6. events (FK → series, venues, scrape_sessions)
7. event_people, person_affiliations, event_organisations
8. event_embeddings, concept_embeddings (need vector extension)
9. sites, tier_runs, scrape_queue
10. (v2 only) concepts, concept_edges (Apache AGE relational shadow — arch.md §1 Q4)

### Extensions to enable first
```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- AGE is v2 only. v1 uses the pgvector/pgvector:pg16 image which has no AGE.
-- The v1 init_db() skips these three lines entirely. arch.md §6 has details.
-- v2 (when AGE is added back):
--   CREATE EXTENSION IF NOT EXISTS age;
--   LOAD 'age';
--   SET search_path = ag_catalog, "$user", public;
--   SELECT create_graph('cfp_graph');
```

### Full DDL (implement exactly)

```sql
CREATE TABLE IF NOT EXISTS organisations (
    org_id   SERIAL PRIMARY KEY,
    name     VARCHAR NOT NULL,
    type     VARCHAR,
    country  VARCHAR,
    website  VARCHAR
);

CREATE TABLE IF NOT EXISTS series (
    series_id   INTEGER PRIMARY KEY,
    acronym     VARCHAR NOT NULL,
    full_name   VARCHAR,
    origin_url VARCHAR,
    org_id      INTEGER REFERENCES organisations(org_id)
);

CREATE TABLE IF NOT EXISTS venues (
    venue_id  SERIAL PRIMARY KEY,
    name      VARCHAR,
    city      VARCHAR,
    state     VARCHAR,
    country   VARCHAR,
    latitude  DOUBLE PRECISION,
    longitude DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS people (
    person_id SERIAL PRIMARY KEY,
    full_name VARCHAR NOT NULL,
    email     VARCHAR,
    dblp_url  VARCHAR,
    homepage  VARCHAR
);

CREATE TABLE IF NOT EXISTS scrape_sessions (
    session_id     VARCHAR PRIMARY KEY,    -- e.g. "2026-04-26T03:00Z-gpu_mid-abc123"
    started_at     TIMESTAMPTZ DEFAULT NOW(),
    finished_at    TIMESTAMPTZ,
    machine        VARCHAR,                 -- CFP_MACHINE profile name
    git_sha        VARCHAR,                 -- commit hash of the runner
    prompts_md_sha VARCHAR,                 -- sha1(prompts.md) — pin prompt version
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_id           INTEGER PRIMARY KEY,
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
    paper_deadline     DATE,                  -- canonical name (was "deadline")
    notification       DATE,
    camera_ready       DATE,
    where_raw          VARCHAR,
    country            VARCHAR,
    region             VARCHAR,
    india_state        VARCHAR,
    venue_id           INTEGER REFERENCES venues(venue_id),
    origin_url        VARCHAR,
    official_url       VARCHAR,
    submission_system  VARCHAR,                -- EasyChair/EDAS/HotCRP/CMT/OpenReview link
    sponsor_names      VARCHAR[],              -- IEEE/ACM/Springer/etc. sponsor tags
    raw_tags           VARCHAR[],
    raw_cfp_text       TEXT,
    description        VARCHAR,
    notes              TEXT DEFAULT '',
    rank               VARCHAR,
    source             VARCHAR DEFAULT 'wikicfp',
    quality_flags      VARCHAR[],              -- output of PROMPT_QUALITY_GUARD
    quality_severity   VARCHAR,                -- "block" | "warn" | "ok"
    scrape_session_id  VARCHAR REFERENCES scrape_sessions(session_id),
    scraped_at         TIMESTAMPTZ DEFAULT NOW(),
    last_checked       TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS event_people (
    event_id  INTEGER REFERENCES events(event_id),
    person_id INTEGER REFERENCES people(person_id),
    role      VARCHAR,
    PRIMARY KEY (event_id, person_id, role)
);

CREATE TABLE IF NOT EXISTS person_affiliations (
    person_id INTEGER REFERENCES people(person_id),
    org_id    INTEGER REFERENCES organisations(org_id),
    year      INTEGER,
    role      VARCHAR,
    PRIMARY KEY (person_id, org_id, year)
);

CREATE TABLE IF NOT EXISTS event_organisations (
    event_id INTEGER REFERENCES events(event_id),
    org_id   INTEGER REFERENCES organisations(org_id),
    role     VARCHAR,
    PRIMARY KEY (event_id, org_id, role)
);

CREATE TABLE IF NOT EXISTS event_embeddings (
    event_id  INTEGER PRIMARY KEY REFERENCES events(event_id),
    vec       vector(768),
    text_hash VARCHAR
);

CREATE TABLE IF NOT EXISTS concept_embeddings (
    concept_name VARCHAR PRIMARY KEY,
    vec          vector(768)
);

CREATE TABLE IF NOT EXISTS sites (
    domain        VARCHAR PRIMARY KEY,
    parser_type   VARCHAR,
    last_scraped  TIMESTAMPTZ,
    robots_txt    VARCHAR,
    crawl_delay_s INTEGER DEFAULT 5,
    last_cursor   VARCHAR                    -- mirror of cfp:cursor:{source} (Q11)
);

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
    PRIMARY KEY (event_id, tier, ts)
);

CREATE TABLE IF NOT EXISTS scrape_queue (
    url             VARCHAR PRIMARY KEY,
    domain          VARCHAR,
    priority        INTEGER,
    source_event_id INTEGER,
    attempts        INTEGER DEFAULT 0,
    status          VARCHAR DEFAULT 'pending',
    added_at        TIMESTAMPTZ DEFAULT NOW(),
    last_error      VARCHAR
);
```

### Indexes (create after tables)
```sql
CREATE INDEX IF NOT EXISTS idx_events_country        ON events(country);
CREATE INDEX IF NOT EXISTS idx_events_state          ON events(india_state);
CREATE INDEX IF NOT EXISTS idx_events_start          ON events(start_date);
CREATE INDEX IF NOT EXISTS idx_events_paper_deadline ON events(paper_deadline);
CREATE INDEX IF NOT EXISTS idx_events_categories     ON events USING GIN(categories);
CREATE INDEX IF NOT EXISTS idx_events_tags           ON events USING GIN(raw_tags);
CREATE INDEX IF NOT EXISTS idx_events_sponsors       ON events USING GIN(sponsor_names);
CREATE INDEX IF NOT EXISTS idx_events_session        ON events(scrape_session_id);
CREATE INDEX IF NOT EXISTS idx_people_name           ON people(full_name);
CREATE INDEX IF NOT EXISTS idx_venues_location       ON venues(country, city);
-- IVFFlat: create AFTER inserting 10k+ rows for best performance.
-- Rebuild trigger from arch.md §1 Q8: rebuild whenever row count doubles,
-- using lists = floor(sqrt(rows)).
-- CREATE INDEX ON event_embeddings USING ivfflat (vec vector_cosine_ops) WITH (lists=100);
```

---

## upsert_event() — COALESCE pattern (CRITICAL: never overwrite enriched fields with NULL)

The COALESCE policy:
- **COALESCE (preserve old if new is null)**:
  `official_url`, `notification`, `camera_ready`, `rank`, `description`,
  `submission_system`, `sponsor_names`, `notes`.
  These fields are enriched over multiple scrapes; a re-sync that lacks one
  of them must NOT erase the previously-extracted value.
- **Direct overwrite (always update from latest scrape)**:
  `acronym`, `name`, `edition_year`, `categories`, `is_workshop`, `is_virtual`,
  `when_raw`, `start_date`, `end_date`, `abstract_deadline`, `paper_deadline`,
  `where_raw`, `country`, `region`, `india_state`, `origin_url`,
  `raw_tags`, `quality_flags`, `quality_severity`, `scrape_session_id`,
  `last_checked`.
  These reflect the latest authoritative state — paper_deadline in particular
  must take the latest value (deadline extensions are common; the new value
  is the truth, even when null reverts a stale date).

```python
def upsert_event(conn, event: Event) -> None:
    """
    Insert or update an event. Fields that might be NULL in a new scrape
    (notification, camera_ready, rank, notes, description, submission_system,
    sponsor_names) are preserved from the existing row via COALESCE — a re-sync
    never degrades data quality.

    quality_flags / quality_severity / scrape_session_id always overwrite —
    they reflect the LATEST pipeline run's verdict.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO events (
                event_id, acronym, name, series_id, edition_year,
                categories, is_workshop, is_virtual, when_raw,
                start_date, end_date, abstract_deadline, paper_deadline,
                notification, camera_ready,
                where_raw, country, region, india_state,
                origin_url, official_url, submission_system, sponsor_names,
                raw_tags, description, source,
                quality_flags, quality_severity, scrape_session_id,
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
                paper_deadline    = EXCLUDED.paper_deadline,           -- direct overwrite
                where_raw         = EXCLUDED.where_raw,
                country           = EXCLUDED.country,
                region            = EXCLUDED.region,
                india_state       = EXCLUDED.india_state,
                origin_url       = EXCLUDED.origin_url,
                official_url      = COALESCE(EXCLUDED.official_url,      events.official_url),
                notification      = COALESCE(EXCLUDED.notification,      events.notification),
                camera_ready      = COALESCE(EXCLUDED.camera_ready,      events.camera_ready),
                rank              = COALESCE(EXCLUDED.rank,              events.rank),
                description       = COALESCE(EXCLUDED.description,       events.description),
                submission_system = COALESCE(EXCLUDED.submission_system, events.submission_system),
                sponsor_names     = COALESCE(EXCLUDED.sponsor_names,     events.sponsor_names),
                notes             = COALESCE(NULLIF(events.notes,''),    EXCLUDED.notes, ''),
                raw_tags          = EXCLUDED.raw_tags,
                quality_flags     = EXCLUDED.quality_flags,             -- direct overwrite
                quality_severity  = EXCLUDED.quality_severity,          -- direct overwrite
                scrape_session_id = EXCLUDED.scrape_session_id,         -- direct overwrite
                last_checked      = NOW()
        """, event_to_dict(event))
        conn.commit()
```

---

## Function signatures to implement

```python
def init_db() -> None: ...
    # Creates all tables, indexes, extensions. v1: skip AGE setup.

def upsert_event(conn, event: Event) -> None: ...
    # As above

def get_event_by_id(conn, event_id: int) -> Event | None: ...
def get_event_by_slug(conn, slug: str) -> Event | None: ...
def all_events(conn, category=None, upcoming_only=False,
               country=None, limit=None) -> list[Event]: ...
def upsert_person(conn, person: Person) -> int: ...     # returns person_id
def upsert_venue(conn, venue: Venue) -> int: ...        # returns venue_id
def upsert_org(conn, org: Organisation) -> int: ...     # returns org_id
def upsert_series(conn, series: Series) -> None: ...
def link_event_person(conn, event_id, person_id, role: str) -> None: ...
def link_event_org(conn, event_id, org_id, role: str) -> None: ...

def begin_session(conn, machine: str, git_sha: str,
                  prompts_md_sha: str) -> str: ...
    # INSERT a new row in scrape_sessions; return session_id (uuid + timestamp).

def end_session(conn, session_id: str) -> None: ...
    # UPDATE scrape_sessions.finished_at = NOW() WHERE session_id = ...

def seed_from_json(conn, json_path: Path, session_id: str) -> int: ...
    # Import data/latest.json (350 conferences) into PostgreSQL with the given
    # scrape_session_id. Used once during init-db. Returns count of imported records.
```

---

## event_to_dict() helper

```python
from dataclasses import asdict

def event_to_dict(event: Event) -> dict:
    d = asdict(event)
    d["categories"] = [c.value for c in event.categories]
    return d
```
