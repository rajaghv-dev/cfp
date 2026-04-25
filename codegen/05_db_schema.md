# Codegen 05 — wcfp/db.py

## File to Create
- `wcfp/db.py`

## Imports
```python
import psycopg          # psycopg3, NOT psycopg2
from psycopg.rows import dict_row
from config import PG_DSN, AGE_GRAPH
from wcfp.models import Event, Person, Organisation, Venue, Series
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
5. events (FK → series, venues)
6. event_people, person_affiliations, event_organisations
7. event_embeddings, concept_embeddings (need vector extension)
8. sites, tier_runs, scrape_queue

### Extensions to enable first
```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
SELECT create_graph('wcfp_graph');   -- idempotent: wrap in DO $$ BEGIN ... EXCEPTION WHEN ... END $$
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
    wikicfp_url VARCHAR,
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

CREATE TABLE IF NOT EXISTS events (
    event_id         INTEGER PRIMARY KEY,
    acronym          VARCHAR,
    name             VARCHAR,
    series_id        INTEGER REFERENCES series(series_id),
    edition_year     INTEGER,
    categories       VARCHAR[],
    is_virtual       BOOLEAN DEFAULT FALSE,
    when_raw         VARCHAR,
    start_date       DATE,
    end_date         DATE,
    abstract_deadline DATE,
    deadline         DATE,
    notification     DATE,
    camera_ready     DATE,
    where_raw        VARCHAR,
    country          VARCHAR,
    region           VARCHAR,
    india_state      VARCHAR,
    venue_id         INTEGER REFERENCES venues(venue_id),
    wikicfp_url      VARCHAR,
    official_url     VARCHAR,
    raw_tags         VARCHAR[],
    raw_cfp_text     TEXT,
    description      VARCHAR,
    notes            TEXT DEFAULT '',
    rank             VARCHAR,
    source           VARCHAR DEFAULT 'wikicfp',
    scraped_at       TIMESTAMPTZ DEFAULT NOW(),
    last_checked     TIMESTAMPTZ DEFAULT NOW()
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
    crawl_delay_s INTEGER DEFAULT 5
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
CREATE INDEX IF NOT EXISTS idx_events_country   ON events(country);
CREATE INDEX IF NOT EXISTS idx_events_state     ON events(india_state);
CREATE INDEX IF NOT EXISTS idx_events_start     ON events(start_date);
CREATE INDEX IF NOT EXISTS idx_events_deadline  ON events(deadline);
CREATE INDEX IF NOT EXISTS idx_events_categories ON events USING GIN(categories);
CREATE INDEX IF NOT EXISTS idx_events_tags      ON events USING GIN(raw_tags);
CREATE INDEX IF NOT EXISTS idx_people_name      ON people(full_name);
CREATE INDEX IF NOT EXISTS idx_venues_location  ON venues(country, city);
-- IVFFlat: create AFTER inserting 10k+ rows for best performance
-- CREATE INDEX ON event_embeddings USING ivfflat (vec vector_cosine_ops) WITH (lists=100);
```

---

## upsert_event() — COALESCE pattern (CRITICAL: never overwrite enriched fields with NULL)

```python
def upsert_event(conn, event: Event) -> None:
    """
    Insert or update an event. Fields that might be NULL in a new scrape
    (notification, camera_ready, rank, notes, description) are preserved
    from the existing row via COALESCE — a re-sync never degrades data quality.
    """
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO events (
                event_id, acronym, name, series_id, edition_year,
                categories, is_virtual, when_raw,
                start_date, end_date, abstract_deadline, deadline,
                notification, camera_ready,
                where_raw, country, region, india_state,
                wikicfp_url, official_url, raw_tags,
                description, source, scraped_at, last_checked
            ) VALUES (
                %(event_id)s, %(acronym)s, %(name)s, %(series_id)s, %(edition_year)s,
                %(categories)s, %(is_virtual)s, %(when_raw)s,
                %(start_date)s, %(end_date)s, %(abstract_deadline)s, %(deadline)s,
                %(notification)s, %(camera_ready)s,
                %(where_raw)s, %(country)s, %(region)s, %(india_state)s,
                %(wikicfp_url)s, %(official_url)s, %(raw_tags)s,
                %(description)s, %(source)s, NOW(), NOW()
            )
            ON CONFLICT (event_id) DO UPDATE SET
                acronym        = EXCLUDED.acronym,
                name           = EXCLUDED.name,
                edition_year   = EXCLUDED.edition_year,
                categories     = EXCLUDED.categories,
                is_virtual     = EXCLUDED.is_virtual,
                when_raw       = EXCLUDED.when_raw,
                start_date     = EXCLUDED.start_date,
                end_date       = EXCLUDED.end_date,
                abstract_deadline = EXCLUDED.abstract_deadline,
                deadline       = EXCLUDED.deadline,
                where_raw      = EXCLUDED.where_raw,
                country        = EXCLUDED.country,
                region         = EXCLUDED.region,
                india_state    = EXCLUDED.india_state,
                wikicfp_url    = EXCLUDED.wikicfp_url,
                official_url   = COALESCE(EXCLUDED.official_url,   events.official_url),
                notification   = COALESCE(EXCLUDED.notification,   events.notification),
                camera_ready   = COALESCE(EXCLUDED.camera_ready,   events.camera_ready),
                rank           = COALESCE(EXCLUDED.rank,           events.rank),
                description    = COALESCE(EXCLUDED.description,    events.description),
                notes          = COALESCE(NULLIF(events.notes,''), EXCLUDED.notes, ''),
                raw_tags       = EXCLUDED.raw_tags,
                last_checked   = NOW()
        """, event_to_dict(event))
        conn.commit()
```

---

## Function signatures to implement

```python
def init_db() -> None: ...
    # Creates all tables, indexes, extensions, AGE graph

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

def seed_from_json(conn, json_path: Path) -> int: ...
    # Import data/latest.json (350 conferences) into PostgreSQL
    # Used once during init-db. Returns count of imported records.
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
