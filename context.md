# Project Context — WikiCFP Conference Scraper

> Living architecture document. Source of truth for code generation.
> Last updated: 2026-04-25

When in doubt, read this file end-to-end before writing code. Every section
contains concrete contracts (filenames, signatures, schemas, key names) — do
not invent alternatives.

---

## 1. Project Goal

Fully automated pipeline that:

1. Scrapes WikiCFP (keyword search + full A–Z series index + journal index).
2. Follows links to official conference websites and previous-year archives.
3. Deduplicates across all sources using `event_id` + semantic embeddings.
4. Classifies every conference into one or more categories (multi-label).
5. Generates organised Markdown reports per category and region (India broken down state-wise).
6. Refreshes on a cron so past conferences age out of the upcoming section.
7. Builds an OWL ontology from the scraped category tags as a side product.

---

## 2. Hardware Inventory

| Machine       | GPU      | VRAM   | Role                                           |
|---------------|----------|--------|------------------------------------------------|
| Workstation A | RTX 4090 | 24 GB  | Tier 3 reasoning + tool calling                |
| Workstation B | RTX 3080 | 16 GB  | Tier 1 / Tier 2 inference, embeddings          |
| DGX Station   | 8× A100  | 256 GB | Tier 4 batch reasoning, ontology inference     |

Each machine runs its own Ollama daemon. Hosts configured in `config.py` (see §6).

---

## 3. Three-Database Architecture — Roles and Connections

This is the most important section. The three databases serve completely different
purposes and cannot be swapped with each other.

```
Internet ──HTTP──► fetch.py
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  REDIS  — operational nerve system (ephemeral, sub-millisecond)      │
│                                                                      │
│  Scrape URL queue      (sorted set, priority-based)                  │
│  Per-domain rate limiter   (TTL key per domain, SETNX)               │
│  Seen-URL dedup cache  (SETNX before enqueue, 30-day TTL)            │
│  In-flight worker lease    (auto-expiry = crash safety)              │
│  Dead-letter list      (failed jobs after MAX_RETRIES)               │
│  Tier escalation queues    (jobs waiting for next LLM tier)          │
│                                                                      │
│  Owns ZERO persistent business data.                                 │
│  Wipe Redis → no data loss. Re-enqueue from PostgreSQL cursor.       │
└─────────────────────��────┬───────────────────────────────���───────────┘
                            │ dequeue / ack / escalate
                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│  POSTGRESQL 16 — source of truth (persistent, multi-modal)           │
│                                                                      │
│  ┌─────────────┐   ┌──────────────┐   ┌──────────────────────────┐  │
│  │  Relational │   │  pgvector    │   │  Apache AGE extension    │  │
│  │  Tables     │   │  extension   │   │                          │  │
│  │             │   │              │   │  Property Graph          │  │
│  │  events     │   │  768-d float │   │  Cypher queries          │  │
│  │  series     │   │  vectors     │   │                          │  │
│  │  people     │   │  Semantic    │   │  Conference──Person      │  │
│  │  venues     │   │  dedup +     │   │  Venue──City──Country    │  │
│  │  orgs       │   │  similarity  │   │  Concept──is_a──►Concept │  │
│  │  tier_runs  │   │  search      │   │  Organisation            │  │
│  └─────────────┘   └──────────────┘   └──────────────────────────┘  │
│                                                                      │
│  Single source of truth. All writes land here.                       │
│  pg_dump = complete backup. pg_restore = full state restore.         │
└───────────────────────────┬──────────────────────────────────────────┘
                             │ postgres_scanner extension (DuckDB reads PG)
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│  DUCKDB — analytical lens (no storage, reads PostgreSQL)             │
│                                                                      │
│  Connects to PostgreSQL as a foreign source                          │
│  Applies columnar OLAP engine on top of PG data                      │
│  GROUP BY / window functions / aggregations at high speed            │
│  Powers generate_md.py (all Markdown reports)                        │
│  Exports Parquet snapshots for archiving                             │
│                                                                      │
│  Owns NO persistent data. Think of it as a smart calculator.         │
│  Nothing to back up. Nothing to sync.                                │
└──────────────────────────────────────────────────────────────────────┘
```

### In plain English

**Redis** answers: *what should I fetch next, and am I allowed to fetch it now?*
It is the traffic controller. Sorted sets give O(log N) priority dequeue.
TTL keys give free per-domain rate limiting. SETNX gives atomic dedup.
No other system does all three efficiently simultaneously.
Restarting Redis loses only operational state, never business data.

**PostgreSQL** answers: *what do I know, and how does it all connect?*
Every fact lives here permanently — structured rows, JSONB blobs, vector
embeddings, and a full property graph, all queryable together in a single
SQL+Cypher statement. This is the key advantage: one query can filter by
country (SQL), rank by embedding similarity (pgvector), and traverse the
ontology hierarchy (Apache AGE) without leaving the database.

**DuckDB** answers: *what does the data look like in aggregate?*
DuckDB is not a store — it is a calculation engine that reads from PostgreSQL.
PostgreSQL is optimised for transactional row-level writes. DuckDB is optimised
for scanning millions of rows and aggregating. Connecting DuckDB to PostgreSQL
via postgres_scanner gives the best of both worlds: transactional writes in
PostgreSQL, columnar analytical reads via DuckDB. All report generation
uses DuckDB. DuckDB never writes to disk in this project.

---

## 4. Why This Stack Beats the Alternatives

| Need | Winner | Why |
|---|---|---|
| Vector + graph + structured in one query | pgvector + Apache AGE | No other self-hosted option unifies all three |
| Ontology as a live queryable graph | Apache AGE | Cypher traversal; write edges directly; no separate store |
| OLAP report generation | DuckDB on PostgreSQL | Columnar speed without giving up PG as source of truth |
| Queue + per-domain rate limiting | Redis | TTL-based rate limiting is native; O(1) dedup via SETNX |
| Portable backup | pg_dump | Single file; restore on any machine in one command |
| Semantic dedup | pgvector | Colocated with the data it indexes; JOIN in pure SQL |

Why not Qdrant/LanceDB: separate process or file; cannot JOIN with relational
data in SQL; no graph traversal possible.

Why not Neo4j: excellent graphs but no vector search, no relational tables, no
SQL — forces data duplication across systems.

Why not MongoDB: no graph traversal, no native vector search, weaker analytics.


---

## 5. Knowledge Graph Schema (Apache AGE)

One graph: **wcfp_graph**. All Cypher runs via:

```sql
SELECT * FROM cypher('wcfp_graph', $$ <cypher here> $$) AS (col agtype);
```

### Node labels

| Label | Key properties |
|---|---|
| Conference | event_id, acronym, name, edition_year, is_virtual, start_date, end_date |
| ConferenceSeries | series_id, acronym, full_name |
| Person | person_id, full_name, email, dblp_url, homepage |
| Organisation | org_id, name, type (publisher/university/company/research_lab), country |
| Venue | venue_id, name, city, country |
| City | name, country, state |
| Country | iso2, name, region |
| Concept | name (PascalCase), description, depth (0=root, 1=ResearchField, 2=AI ...) |
| RawTag | text (original WikiCFP lowercase category tag) |
| Workshop | event_id, acronym, name |

### Edge types

```
(Conference)       -[:IS_EDITION_OF]->                 (ConferenceSeries)
(Conference)       -[:HELD_AT]->                        (Venue)
(Conference)       -[:CLASSIFIED_AS {conf, tier}]->     (Concept)
(Conference)       -[:CO_LOCATED_WITH]->                (Workshop)
(ConferenceSeries) -[:PRECEDED_BY]->                    (ConferenceSeries)
(Person)           -[:CHAIRS {role}]->                  (Conference)
(Person)           -[:DELIVERS_KEYNOTE_AT]->            (Conference)
(Person)           -[:AFFILIATED_WITH {year, role}]->   (Organisation)
(Organisation)     -[:PUBLISHES]->                      (ConferenceSeries)
(Organisation)     -[:SPONSORS]->                       (Conference)
(Organisation)     -[:ORGANISES]->                      (Conference)
(Venue)            -[:IN_CITY]->                        (City)
(City)             -[:IN_COUNTRY]->                     (Country)
(City)             -[:IN_STATE {state}]->               (Country)
(Concept)          -[:IS_A]->                           (Concept)
(Concept)          -[:PART_OF]->                        (Concept)
(Concept)          -[:RELATED_TO {weight}]->            (Concept)
(RawTag)           -[:SYNONYM_OF]->                     (Concept)
```

### Power queries

```cypher
-- Upcoming ML conferences in Europe
MATCH (c:Conference)-[:CLASSIFIED_AS]->(cls)-[:IS_A*0..]->(root {name:'MachineLearning'})
MATCH (c)-[:HELD_AT]->()-[:IN_CITY]->()-[:IN_COUNTRY]->(co:Country {region:'Europe'})
WHERE c.start_date > date()
RETURN c.acronym, c.name, c.start_date ORDER BY c.start_date

-- Most prolific PC chairs in chip design
MATCH (p:Person)-[:CHAIRS]->(c:Conference)-[:CLASSIFIED_AS]->(:Concept {name:'ChipDesign'})
WITH p, COUNT(c) AS n WHERE n >= 3
MATCH (p)-[:AFFILIATED_WITH]->(o:Organisation)
RETURN p.full_name, o.name, n ORDER BY n DESC LIMIT 20

-- Ontology co-occurrence learning signal
MATCH (c:Conference)-[:CLASSIFIED_AS]->(a:Concept)
MATCH (c)-[:CLASSIFIED_AS]->(b:Concept) WHERE id(a) < id(b)
RETURN a.name, b.name, COUNT(c) AS co ORDER BY co DESC LIMIT 30

-- India conferences by state with PC chair institutions
MATCH (c:Conference)-[:HELD_AT]->()-[:IN_CITY]->(city)-[:IN_STATE]->(co {iso2:'IN'})
MATCH (p:Person)-[:CHAIRS]->(c)-[:AFFILIATED_WITH]->(o:Organisation)
RETURN city.state, c.acronym, p.full_name, o.name ORDER BY city.state
```

Cross-modal query (pgvector + AGE + SQL in one statement):

```sql
-- Upcoming ChipDesign conferences in Europe ranked by embedding similarity
SELECT e.acronym, e.name, 1 - (emb.vec <=> $query_vec) AS similarity
FROM events e
JOIN event_embeddings emb ON e.event_id = emb.event_id
WHERE e.start_date > CURRENT_DATE AND e.region = 'Europe'
  AND e.event_id IN (
    SELECT (row->>'event_id')::int
    FROM cypher('wcfp_graph', $$
      MATCH (c:Conference)-[:CLASSIFIED_AS]->(cls)
            -[:IS_A*0..]->(root {name: 'ChipDesign'})
      RETURN c.event_id
    $$) AS (row agtype)
  )
ORDER BY similarity DESC LIMIT 10;
```

---

## 6. Relational Schema (`wcfp/db.py`)

Key tables: **organisations**, **series** (FK → organisations),
**venues**, **people**, **events** (FK → series, venues),
**event_people**, **person_affiliations**, **event_organisations**.

pgvector tables: **event_embeddings** (vector(768)), **concept_embeddings** (vector(768)).

Infrastructure: **sites**, **tier_runs** (JSONB output_json), **scrape_queue**.

Critical indexes:
- IVFFlat on event_embeddings and concept_embeddings (create after 10k+ rows)
- GIN on events.categories[] and events.raw_tags[]
- B-tree on events(country), events(india_state), events(start_date), events(deadline)

Upserts use `INSERT ... ON CONFLICT (pk) DO UPDATE SET ...`

---

## 7. DuckDB as Analytics Layer (`wcfp/analytics.py`)

DuckDB owns NO storage. It attaches to PostgreSQL via postgres_scanner
and runs its columnar OLAP engine on the data.

```python
import duckdb
from config import PG_DSN

def get_analytics_conn():
    conn = duckdb.connect()                          # in-memory only
    conn.execute("INSTALL postgres; LOAD postgres;")
    conn.execute(f"ATTACH '{PG_DSN}' AS pg (TYPE POSTGRES, READ_ONLY)")
    return conn
```

All `generate_md.py` reporting uses DuckDB.
All writes use `psycopg3` directly against PostgreSQL.
DuckDB never writes to disk in this project — it is a calculation layer only.

---

## 8. Redis Key Schema (`wcfp/queue.py`)

| Key pattern                | Type       | TTL           | Purpose                                           |
|----------------------------|------------|---------------|---------------------------------------------------|
| `wcfp:queue`               | sorted set | none          | Priority queue (score = priority×1e10 + epoch_ms) |
| `wcfp:inflight:{job_id}`   | string     | 600 s         | Worker lease; expiry auto-returns job to queue    |
| `wcfp:seen:{sha1(url)}`    | string "1" | 30 days       | SETNX dedup before enqueue                        |
| `wcfp:rate:{domain}`       | string "1" | crawl_delay_s | SETNX rate limiter per domain                     |
| `wcfp:robots:{domain}`     | string     | 1 day         | Cached robots.txt                                 |
| `wcfp:dead`                | list       | none          | RPUSH after MAX_RETRIES                           |
| `wcfp:escalate:tier{N}`    | list       | none          | Jobs awaiting next LLM tier                       |
| `wcfp:metrics:tier{N}`     | hash       | none          | ok / escalated / failed counters                  |
| `wcfp:cursor:{source}`     | string     | none          | Resume page cursor per source                     |

---

## 9. Model Roster and Tool Calling

| Model              | Host     | VRAM    | Tool calling | Role                                     |
|--------------------|----------|---------|--------------|------------------------------------------|
| `qwen3:4b`         | RTX 3080 | ~3 GB   | **YES**      | Tier 1 triage                            |
| `qwen3:14b`        | RTX 3080 | ~10 GB  | **YES**      | Tier 2 extraction + people/venue/org     |
| `qwen3:32b`        | RTX 4090 | ~22 GB  | **YES**      | Tier 3 + unknown site tool calling       |
| `mistral-nemo:12b` | RTX 3080 | ~9 GB   | partial      | Long-context HTML (>32k tokens)          |
| `deepseek-r1:32b`  | RTX 4090 | ~22 GB  | **NO**       | Dedup pair reasoning (pure yes/no)       |
| `deepseek-r1:70b`  | DGX      | ~80 GB  | **NO**       | Tier 4 overnight + ontology inference    |
| `nomic-embed-text` | RTX 3080 | ~300 MB | N/A          | 768-d embeddings → pgvector              |

**Tool calling is used only for unknown external conference websites.**
WikiCFP pages use rule-based BS4 parsing — no GPU, no LLM.
Tools: `extract_text(selector)`, `find_links(pattern)`, `get_field(label)`,
`is_conference_page()`, `classify_category(text)`, `detect_virtual(text)`.
DeepSeek-R1 models do NOT need tools; their tasks are structured reasoning over
data already in the database (dedup comparison, ontology validation).

---

## 10. Module Layout

```
wiki-cfp/
├── config.py            PG_DSN, REDIS_URL, OLLAMA_HOSTS, MODEL_HOST,
│                        TIER_THRESHOLD, EMBED_DIM, AGE_GRAPH, WCFP_MACHINE
├── prompts.md           search queries + LLM prompt bodies (machine-read)
├── context.md           this file
├── docker-compose.yml   PostgreSQL 16 + AGE + Redis (two containers)
├── requirements.txt
├── wcfp/
│   ├── models.py        Event, Person, Organisation, Venue, Series,
│   │                    ScrapeJob, TierResult, EscalationPayload, OntologyEdge
│   ├── prompts_parser.py  parse_prompts_md(path) -> ParsedPrompts
│   ├── db.py            PostgreSQL CRUD via psycopg3
│   ├── graph.py         Apache AGE: sync nodes/edges, cypher_query helper
│   ├── analytics.py     DuckDB attached to PostgreSQL + export_parquet
│   ├── queue.py         Redis: enqueue, dequeue, rate_limit, dead_letter
│   ├── vectors.py       pgvector: upsert_embedding, search_similar
│   ├── embed.py         nomic-embed-text via Ollama -> list[float]
│   ├── fetch.py         HTTP session, robots.txt, human_delay, with_retry
│   ├── sync.py          pull_state / push_state via rclone
│   ├── parsers/
│   │   ├── __init__.py  KNOWN_PARSERS dict, dispatch(domain, html)
│   │   ├── wikicfp.py   search results, event detail, series index,
│   │   │                PC chairs, venue, org extraction
│   │   ├── ieee.py, acm.py, springer.py, usenix.py
│   ├── llm/
│   │   ├── client.py    OllamaClient: chat() + chat_with_tools() loop
│   │   ├── tools.py     TOOLS list + closure-bound implementations
│   │   ├── tier1..4.py
│   ├── dedup.py         pgvector ANN + deepseek-r1:32b confirmation
│   ├── ontology.py      AGE graph -> owlready2 -> .owl export for Protege
│   ├── pipeline.py      orchestrator
│   └── cli.py           python -m wcfp <command>
├── generate_md.py       DuckDB analytics -> reports/*.md
├── reports/
├── data/
│   ├── archive/         Parquet snapshots
│   └── pg_backup/       pg_dump output (synced to cloud via rclone)
└── ontology/
    ├── conference_domain.owl
    ├── concepts.json, edges.json, by_conference.json
```

Import rule: `models.py` and `config.py` import nothing project-internal.

---

## 11. Data Models (`wcfp/models.py`)

Key dataclasses (all `@dataclass(slots=True)`):
`Event`, `Person`, `Organisation`, `Venue`, `Series`, `ScrapeJob`,
`TierResult`, `EscalationPayload`, `OntologyEdge`.

Enums: `Category` (AI/ML/DevOps/...), `Tier` (1-4), `JobStatus`,
`PersonRole` (general_chair/pc_chair/area_chair/keynote),
`OrgType` (publisher/university/company/research_lab/government).

---

## 12. Configuration (`config.py`)

```python
PG_DSN         = os.getenv("PG_DSN", "postgresql://wcfp:wcfp@localhost:5432/wikicfp")
REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379/0")
AGE_GRAPH      = "wcfp_graph"
EMBED_DIM      = 768
DEDUP_COSINE   = 0.92
TIER_THRESHOLD = {1: 0.85, 2: 0.85, 3: 0.80}
LONG_CONTEXT_TOKENS = 32_000
OLLAMA_HOSTS   = {"rtx4090": ..., "rtx3080": ..., "dgx": ...}
MODEL_HOST     = {"qwen3:4b": "rtx3080", "qwen3:32b": "rtx4090", ...}
WCFP_MACHINE   = os.getenv("WCFP_MACHINE", "rtx3080")   # rtx3080|rtx4090|dgx
WCFP_STORAGE   = os.getenv("WCFP_STORAGE", "b2")        # b2|gcs|s3|minio
```

---

## 13. Tiered Curation Pipeline

```
TIER 1  qwen3:4b   RTX 3080  ~200 rec/min
        Output: {is_cfp, categories, is_virtual, confidence}
        conf >= 0.85  →  write to PostgreSQL
        conf <  0.85  →  Redis wcfp:escalate:tier2

TIER 2  qwen3:14b  RTX 3080
        Output: full Event + Person[] + Venue + Organisation[]
        conf >= 0.85  →  PostgreSQL + graph.py syncs to AGE
        conf <  0.85  →  Redis wcfp:escalate:tier3

TIER 3  qwen3:32b  RTX 4090  (tool calling for unknown sites)
        Output: Event + archive_urls + tool_trace
        conf >= 0.80  →  PostgreSQL + AGE
        conf <  0.80  →  Redis wcfp:escalate:tier4

TIER 4  deepseek-r1:70b  DGX  (overnight, no tool calling)
        Output: final Event + OntologyEdge[] + dedup:{same, reason}
        Always final.
```

After every write: `graph.py` syncs new/updated nodes and edges to AGE.

---

## 14. Ontology Pipeline

```
Tier 1  →  raw_tags extracted → stored as RawTag nodes in AGE
Tier 2  →  RawTag-[:SYNONYM_OF]->Concept  (pgvector cosine clustering)
Tier 3  →  Concept-[:IS_A]->Concept       (Cypher co-occurrence + qwen3:32b)
Tier 4  →  hierarchy validation + new branches  (deepseek-r1:70b)
ontology.py  →  walks AGE via Cypher → owlready2 → .owl file (Protege)
```

The AGE graph IS the live ontology. The `.owl` file is a read-only export.

---

## 15. Error Handling, Sync, Installation

**Error classes**: `RetryableError` (429/5xx/timeout/LLM JSON decode) and
`FatalError` (404/410/robots disallow). Both push to `wcfp:dead` after MAX_RETRIES.
Nightly Tier 4 batch drains dead-letter.

**Portable clone**:
```bash
git clone <repo> && cd wiki-cfp
WCFP_MACHINE=rtx3080 WCFP_STORAGE=b2 bash setup.sh
```
setup.sh: venv + pip → docker compose up → rclone pull pg_backup →
pg_restore → ollama pull (machine-specific models) → verify connectivity.

After each run: `pg_dump` → rclone push. `reports/` + `data/latest.json` → git push.
Heavy data (pg_dump, Parquet archives) → rclone to cloud. Never to git.

**requirements.txt**: `psycopg[binary]>=3.1 duckdb>=1.0 redis>=5.0 ollama>=0.3
beautifulsoup4>=4.12 lxml>=5.0 requests>=2.32 tiktoken>=0.7 owlready2>=0.46 rdflib>=7.0 pandas>=2.0`

**docker-compose.yml**: `apache/age:PG16_latest` (PostgreSQL 16 + AGE pre-installed)
+ `redis:7-alpine`. Two containers total.

---

## 16. CLI (`python -m wcfp <command>`)

| Command            | Action                                                          |
|--------------------|-----------------------------------------------------------------|
| `init-db`          | CREATE tables + extensions + AGE graph `wcfp_graph`            |
| `enqueue-seeds`    | Parse prompts.md, push all search/index URLs to Redis           |
| `run-pipeline`     | Long-running: dequeue → fetch → parse → tiers → PG + AGE      |
| `tier4-batch`      | Drain wcfp:dead + escalations on DGX overnight                  |
| `dedup-sweep`      | Recompute pgvector embeddings, run pairwise dedup               |
| `build-ontology`   | Walk AGE graph → owlready2 → ontology/                         |
| `generate-reports` | DuckDB analytics → reports/*.md                                 |
| `sync-push`        | pg_dump + rclone push to cloud storage                          |
| `sync-pull`        | rclone pull + pg_restore from cloud storage                     |
| `replay-dead`      | Pop N from wcfp:dead, re-enqueue at original priority           |

---

## 17. Key Decisions Log

| Date       | Decision                                  | Reason                                                        |
|------------|-------------------------------------------|---------------------------------------------------------------|
| 2026-04-25 | PostgreSQL + pgvector + Apache AGE        | Only stack unifying vector + graph + relational in one query  |
| 2026-04-25 | DuckDB as analytics layer (no storage)    | Columnar OLAP via postgres_scanner; zero extra persistent store|
| 2026-04-25 | Redis for queue + rate limiting only      | TTL rate limiting + crash-safe inflight leases native to Redis|
| 2026-04-25 | AGE graph IS the live ontology            | Cypher traversal; write edges directly; no separate RDF store |
| 2026-04-25 | owlready2/rdflib as export-only layer     | Protege-compatible .owl from AGE graph; not the primary store |
| 2026-04-25 | People/Venue/Org as first-class entities  | PC chair networks, venue history, org sponsorship graphs      |
| 2026-04-25 | pgvector IVFFlat index                    | ANN search; create after 10k+ rows for best performance       |
| 2026-04-25 | apache/age:PG16 Docker image              | Pre-installs AGE + PG16; no manual extension build needed     |
| 2026-04-25 | Qwen3 for ALL tool calling                | Only local family with reliable Ollama tool-call support      |
| 2026-04-25 | DeepSeek-R1 for pure reasoning only       | Best accuracy for yes/no decisions; no tool overhead needed   |
| 2026-04-25 | rclone + Backblaze B2 for state sync      | Cheapest S3-compatible; handles pg_dump + Parquet archives    |
| 2026-04-25 | WCFP_MACHINE env var for model routing    | Each machine pulls only the models it needs                   |
| 2026-04-25 | 4-tier pipeline: 4b → 14b → 32b → 70b   | ~80% resolved by qwen3:4b; DGX only for the hard 1%          |
