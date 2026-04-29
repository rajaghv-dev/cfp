# Project Context — WikiCFP Conference Scraper

> Living architecture document. Source of truth for code generation.
> Last updated: 2026-04-26

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
7. **[v2]** Builds an OWL ontology from the scraped category tags as a side product.

---

## 2. Single-Machine Operation

The pipeline runs on **one machine at a time**. Any machine with Docker and Ollama
installed can restore state from GCS, run a session, sync back, and wipe local data.

Set `CFP_MACHINE` to the profile that matches the current machine:

| `CFP_MACHINE` | Min VRAM | Local tiers                                          |
|----------------|----------|------------------------------------------------------|
| `dgx`          | 80 GB    | All tiers + deepseek-r1:70b for Tier 4               |
| `gpu_large`    | 24 GB    | Tiers 1–4 (qwen3:4b + qwen3:14b + qwen3:32b + deepseek-r1:32b) |
| `gpu_mid`      | 10 GB    | Tiers 1–2 (qwen3:4b + qwen3:14b)                    |
| `gpu_small`    | 4 GB     | Tier 1 only (qwen3:4b)                               |
| `cpu_only`     | —        | Tier 1 only (qwen3:4b, slow)                         |

`nomic-embed-text` runs on all profiles (300 MB VRAM / CPU fallback).

Jobs whose required model is absent are pushed to `cfp:escalate:tier4` and
accumulate until the next session on a capable machine. Nothing is lost.

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

### GCS as Off-Machine Persistence

All local databases are ephemeral — they exist only for the duration of a pipeline
session. GCS (`gs://$GCS_BUCKET/$GCS_PREFIX/`) is the durable store between sessions:

| Artifact            | Local path              | GCS path                         | Tool           |
|---------------------|-------------------------|----------------------------------|----------------|
| PostgreSQL dump     | data/pg_backup/latest.dump | pg_backup/latest.dump         | pg_dump/rclone |
| Parquet snapshots   | data/archive/           | archive/                         | rclone         |
| Reports             | reports/                | reports/                         | rclone + git   |

Redis and DuckDB have no GCS backup: Redis is ephemeral by design; DuckDB owns no data.

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

One graph: **cfp_graph**. All Cypher runs via:

```sql
SELECT * FROM cypher('cfp_graph', $$ <cypher here> $$) AS (col agtype);
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
| Workshop | (v2 only — dropped in v1; use is_workshop flag on Conference node instead) |

### Edge types

```
(Conference)       -[:IS_EDITION_OF]->                 (ConferenceSeries)
(Conference)       -[:HELD_AT]->                        (Venue)
(Conference)       -[:CLASSIFIED_AS {conf, tier}]->     (Concept)
(Conference)       -[:CO_LOCATED_WITH]->                (Conference {is_workshop:true})  # parent conference to workshop — v1 uses is_workshop flag
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
    FROM cypher('cfp_graph', $$
      MATCH (c:Conference)-[:CLASSIFIED_AS]->(cls)
            -[:IS_A*0..]->(root {name: 'ChipDesign'})
      RETURN c.event_id
    $$) AS (row agtype)
  )
ORDER BY similarity DESC LIMIT 10;
```

---

## 6. Relational Schema (`cfp/db.py`)

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

## 7. DuckDB as Analytics Layer (`cfp/analytics.py`)

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

## 8. Redis Key Schema (`cfp/queue.py`)

| Key pattern                | Type       | TTL           | Purpose                                           |
|----------------------------|------------|---------------|---------------------------------------------------|
| `cfp:queue`               | sorted set | none          | Priority queue (score = priority×1e10 + epoch_ms) |
| `cfp:inflight:{job_id}`   | string     | 600 s         | Worker lease; expiry auto-returns job to queue    |
| `cfp:seen:{sha1(url)}`    | string "1" | 30 days       | SETNX dedup before enqueue                        |
| `cfp:rate:{domain}`       | string "1" | crawl_delay_s | SETNX rate limiter per domain                     |
| `cfp:robots:{domain}`     | string     | 1 day         | Cached robots.txt                                 |
| `cfp:dead`                | list       | none          | RPUSH after MAX_RETRIES                           |
| `cfp:escalate:tier{N}`    | list       | none          | Jobs awaiting next LLM tier                       |
| `cfp:metrics:tier{N}`     | hash       | none          | ok / escalated / failed counters                  |
| `cfp:cursor:{source}`     | string     | none          | Resume page cursor per source                     |

---

## 9. Model Roster and Tool Calling

> Model names below are abstract. **Actual pulled tags include pinned quantisation**
> per profile — see `PROFILE_MODELS` in `codegen/01_config_models.md` (q4_K_M
> default for non-DGX profiles, q8_0 on DGX). Resolution: `arch.md §1 Q14`.

| Model              | Min VRAM | Profile      | Tool calling | Role                                     |
|--------------------|----------|--------------|--------------|------------------------------------------|
| `qwen3:4b`         | ~3 GB    | gpu_small+   | **YES**      | Tier 1 triage                            |
| `qwen3:14b`        | ~10 GB   | gpu_mid+     | **YES**      | Tier 2 extraction + people/venue/org     |
| `qwen3:32b`        | ~22 GB   | gpu_large+   | **YES**      | Tier 3 + unknown site tool calling       |
| `mistral-nemo:12b` | ~9 GB    | gpu_mid+     | partial      | Long-context HTML (>32k tokens)          |
| `deepseek-r1:32b`  | ~22 GB   | gpu_large+   | **NO**       | Dedup pair reasoning (pure yes/no)       |
| `deepseek-r1:70b`  | ~80 GB   | dgx only     | **NO**       | Tier 4 batch + ontology inference        |
| `nomic-embed-text` | ~300 MB  | all profiles | N/A          | 768-d embeddings → pgvector              |

Tool calling is used only for unknown external conference websites.
WikiCFP pages use rule-based BS4 parsing — no GPU, no LLM.
Tools: `extract_text(selector)`, `find_links(pattern)`, `get_field(label)`,
`is_conference_page()`, `classify_category(text)`, `detect_virtual(text)`.
DeepSeek-R1 models do NOT use tools; their tasks are pure reasoning over
data already in the database (dedup comparison, ontology validation).

---

## 10. Module Layout

```
wiki-cfp/
├── config.py            PG_DSN, REDIS_URL, OLLAMA_HOST, CFP_MACHINE, PROFILE_MODELS,
│                        TIER_THRESHOLD, EMBED_DIM, AGE_GRAPH
├── prompts.md           search queries + LLM prompt bodies (machine-read)
├── context.md           this file
├── docker-compose.yml   PostgreSQL 16 + pgvector + Redis (v1; v2 swaps to apache/age)
├── requirements.txt
├── cfp/
│   ├── models.py        Event, Person, Organisation, Venue, Series,
│   │                    ScrapeJob, TierResult, EscalationPayload, OntologyEdge
│   ├── prompts_parser.py  parse_prompts_md(path) -> ParsedPrompts
│   ├── db.py            PostgreSQL CRUD via psycopg3
│   ├── graph.py         (v2: Apache AGE) sync nodes/edges, cypher_query helper
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
│   ├── dedup.py         (v1: pgvector-only; v2 adds DeepSeek-R1) pgvector ANN + deepseek-r1:32b confirmation
│   ├── ontology.py      (v2: AGE→owlready2→.owl) AGE graph -> owlready2 -> .owl export for Protege
│   ├── pipeline.py      orchestrator
│   └── cli.py           python -m cfp <command>
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

## 11. Data Models (`cfp/models.py`)

Key dataclasses (all `@dataclass(slots=True)`):
`Event`, `Person`, `Organisation`, `Venue`, `Series`, `ScrapeJob`,
`TierResult`, `EscalationPayload`, `OntologyEdge`.

Enums: `Category` (AI/ML/DevOps/...), `Tier` (1-4), `JobStatus`,
`PersonRole` (general_chair/pc_chair/area_chair/keynote),
`OrgType` (publisher/university/company/research_lab/government).

---

## 12. Configuration (`config.py`)

```python
PG_DSN         = os.getenv("PG_DSN", "postgresql://cfp:cfp@localhost:5432/cfp")
REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379/0")
OLLAMA_HOST    = os.getenv("OLLAMA_HOST", "http://localhost:11434")   # single local daemon
AGE_GRAPH      = "cfp_graph"
EMBED_DIM      = 768
DEDUP_COSINE   = 0.92
TIER_THRESHOLD = {1: 0.85, 2: 0.85, 3: 0.80}
LONG_CONTEXT_TOKENS = 32_000

# Machine profile — controls which models are pulled and which tiers run locally
CFP_MACHINE   = os.getenv("CFP_MACHINE", "gpu_mid")   # dgx|gpu_large|gpu_mid|gpu_small|cpu_only

# GCS / rclone settings
GCS_BUCKET     = os.getenv("GCS_BUCKET", "cfp-data")
GCS_PREFIX     = os.getenv("GCS_PREFIX", "prod")
RCLONE_REMOTE  = os.getenv("RCLONE_REMOTE", "gcs")      # name of the rclone remote

# Model selection by profile — code skips unavailable tiers and escalates
PROFILE_MODELS = {
    "dgx":       ["qwen3:4b","qwen3:14b","qwen3:32b","deepseek-r1:32b","deepseek-r1:70b","nomic-embed-text"],
    "gpu_large": ["qwen3:4b","qwen3:14b","qwen3:32b","deepseek-r1:32b","nomic-embed-text"],
    "gpu_mid":   ["qwen3:4b","qwen3:14b","nomic-embed-text"],
    "gpu_small": ["qwen3:4b","nomic-embed-text"],
    "cpu_only":  ["qwen3:4b","nomic-embed-text"],
}
```

---

## 13. Tiered Curation Pipeline

```
TIER 1  qwen3:4b   gpu_small+ profile  ~200 rec/min
        Output: {is_cfp, categories, is_virtual, confidence}
        conf >= 0.85  →  write to PostgreSQL
        conf <  0.85  →  Redis cfp:escalate:tier2

TIER 2  qwen3:14b  gpu_mid+ profile
        Output: full Event + Person[] + Venue + Organisation[]
        conf >= 0.85  →  PostgreSQL + graph.py syncs to AGE
        conf <  0.85  →  Redis cfp:escalate:tier3

TIER 3  qwen3:32b  gpu_large+ profile  (tool calling for unknown sites)
        Output: Event + archive_urls + tool_trace
        conf >= 0.80  →  PostgreSQL + AGE
        conf <  0.80  →  Redis cfp:escalate:tier4

TIER 4  deepseek-r1:70b  dgx profile  (overnight batch, no tool calling)
        Output: final Event + OntologyEdge[] + dedup:{same, reason}
        Always final.
```

After every write: `graph.py` syncs new/updated nodes and edges to AGE.

---

## 14. Ontology Pipeline

> **v2 only.** v1 stores raw_tags only. Ontology pipeline runs after AGE migration.

```
Tier 1  →  raw_tags extracted → stored as RawTag nodes in AGE
Tier 2  →  RawTag-[:SYNONYM_OF]->Concept  (pgvector cosine clustering)
Tier 3  →  Concept-[:IS_A]->Concept       (Cypher co-occurrence + qwen3:32b)
Tier 4  →  hierarchy validation + new branches  (deepseek-r1:70b)
ontology.py  →  walks AGE via Cypher → owlready2 → .owl file (Protege)
```

The AGE graph IS the live ontology. The `.owl` file is a read-only export.

---

## 15. Error Handling and Portable Install

**Error classes**: `RetryableError` (429/5xx/timeout/LLM JSON decode) and
`FatalError` (404/410/robots disallow). Both push to `cfp:dead` after MAX_RETRIES.
The next Tier 4 batch drains the dead-letter list.

**Portable clone (any machine)**:
```bash
git clone <repo> && cd wiki-cfp
CFP_MACHINE=gpu_large GCS_BUCKET=cfp-data bash setup.sh
```
setup.sh sequence: venv + pip → docker compose up → rclone pull pg_backup →
pg_restore → ollama pull (profile-specific models) → verify connectivity.

**requirements.txt**: `psycopg[binary]>=3.1 aiohttp>=3.9 redis>=5.0 ollama>=0.3
beautifulsoup4>=4.12 lxml>=5.0 requests>=2.32 tiktoken>=0.7 python-dateutil>=2.9
duckdb>=1.0 pandas>=2.0 typer>=0.12 rich>=13.0`
v2 only: owlready2>=0.46 rdflib>=7.0

**docker-compose.yml**: `pgvector/pgvector:pg16` (PostgreSQL 16 + pgvector — v1).
v2 switches to `apache/age:PG16_latest` when AGE is added.
+ `redis:7-alpine`. Two containers only. All state in named Docker volumes.
`docker compose down -v` removes all local state — GCS is the backup.

---

## 16. CLI (`python -m cfp <command>`)

| Command            | Action                                                          |
|--------------------|-----------------------------------------------------------------|
| `init-db`          | CREATE tables + extensions + AGE graph `cfp_graph`             |
| `enqueue-seeds`    | Parse prompts.md, push all search/index URLs to Redis           |
| `run-pipeline`     | Long-running: dequeue → fetch → parse → tiers → PG + AGE      |
| `tier4-batch`      | Drain cfp:dead + escalations on DGX overnight (v2: requires deepseek-r1:70b on dgx/gpu_large) |
| `dedup-sweep`      | Recompute pgvector embeddings, run pairwise dedup (v1: pgvector only; v2: adds DeepSeek-R1 confirmation) |
| `build-ontology`   | Walk AGE graph → owlready2 → ontology/ (v2: requires Apache AGE) |
| `generate-reports` | DuckDB analytics → reports/*.md                                 |
| `sync-push`        | pg_dump + rclone push to cloud storage                          |
| `sync-pull`        | rclone pull + pg_restore from cloud storage                     |
| `replay-dead`      | Pop N from cfp:dead, re-enqueue at original priority           |

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
| 2026-04-25 | CFP_MACHINE env var for model routing    | Each machine pulls only the models it needs                   |
| 2026-04-25 | 4-tier pipeline: 4b → 14b → 32b → 70b   | ~80% resolved by qwen3:4b; DGX only for the hard 1%          |
| 2026-04-26 | Single-machine operation + GCS persistence | Any machine can run; GCS is the durable store; local state is always ephemeral |
| 2026-04-26 | CFP_MACHINE profile replaces per-host routing | One Ollama daemon on localhost; tiers skipped if model absent; jobs escalate |
| 2026-04-26 | v1 ships without AGE, DuckDB, Tier 3+4, ontology | Faster path to real data; v2 adds these as an additive migration — arch.md §6 |
| 2026-04-26 | v1 Docker image: pgvector/pgvector:pg16 (not apache/age) | AGE adds extension complexity; defer until ontology pipeline is needed |
| 2026-04-29 | Ollama: named volume `ollama_models:/root/.ollama` for local Compose (Q10) | One-time model pull; reuse across restarts; GKE pre-baked images deferred to v2 |
| 2026-04-29 | LLM JSON failures: local repair → 1 same-tier retry → escalate (Q12) | Balances cost vs correctness; parse-fail rate tracked per model |
| 2026-04-29 | Pinned quant tags in PROFILE_MODELS: q4_K_M default, q8_0 on dgx (Q14) | Prevents Ollama silently degrading JSON validity on small VRAM |
| 2026-04-29 | Renamed project identifiers: cfp (not wcfp/wikicfp) throughout | Consistent branding; wcfp/wikicfp only remain as external website references |

---

## 18. Machine Lifecycle — Pull → Run → Sync → Wipe

Every pipeline session follows four phases. `setup.sh` automates phases 1 and 4.

### Phase 1 — Restore
```bash
docker compose up -d
rclone copy $RCLONE_REMOTE:$GCS_BUCKET/$GCS_PREFIX/pg_backup/ ./data/pg_backup/
pg_restore -h localhost -U cfp -d cfp -F c ./data/pg_backup/latest.dump
ollama pull $(python -m cfp list-models)   # pulls only what CFP_MACHINE profile needs
```

### Phase 2 — Run
```bash
python -m cfp init-db           # idempotent: skips if schema exists
python -m cfp enqueue-seeds     # parse prompts.md → push URLs into Redis queue
python -m cfp run-pipeline      # dequeue → fetch → parse → tier pipeline → PG + AGE
python -m cfp generate-reports  # DuckDB → reports/*.md
```

### Phase 3 — Sync
```bash
python -m cfp sync-push         # internally runs:
  # pg_dump -F c -f ./data/pg_backup/latest.dump
  # rclone copy ./data/pg_backup/ $RCLONE_REMOTE:$GCS_BUCKET/$GCS_PREFIX/pg_backup/
  # rclone copy ./reports/        $RCLONE_REMOTE:$GCS_BUCKET/$GCS_PREFIX/reports/
  # git add reports/ data/latest.json && git commit && git push
```

### Phase 4 — Wipe (recommended for borrowed or cloud machines)
```bash
docker compose down -v           # removes PostgreSQL + Redis volumes
rm -rf ./data/pg_backup/         # remove local dump file
ollama rm qwen3:32b deepseek-r1:32b   # optional: free disk on profiles that won't reuse them
```

**Nothing is lost.** GCS holds the canonical pg_dump. Git holds reports and seed data.
Re-running `bash setup.sh` on any machine restores full operational state.

---

## 19. Open Architectural Questions

This section is a brief index of unresolved architectural questions that must be
answered before the implementation is correct. Full analysis (with candidate
answers, trade-offs, and recommendations) lives in `arch.md` Section 1.

**Q1. PostgreSQL lifecycle.** Cloud SQL is incompatible with Apache AGE; Docker
Compose works for one machine but cannot support concurrent sessions. The
recommendation is to keep Docker Compose for local dev and add a StatefulSet+PVC
variant for the future GKE pull→run→wipe Job. See `arch.md §1 Q1`.

**Q2. Dedup trigger timing.** The spec says "pgvector ANN + DeepSeek-R1
confirmation" but never says when. Recommendation: synchronous high-threshold
(0.97) ANN check skips writes; 0.92–0.97 enqueue for async LLM confirmation;
nightly `dedup-sweep` is the safety net. Add `DEDUP_AUTO_MERGE = 0.97` to
`config.py`. See `arch.md §1 Q2`.

**Q3. Ontology bootstrap.** First run has no Concept nodes; clustering and IS_A
inference both need seeds. Recommendation: seed the 13 Category enum values as
root Concepts under a `ResearchField` super-root, plus `RELATED_TO` cross-edges
for cross-cutting concepts. Add a one-time `bootstrap-ontology` CLI command.
See `arch.md §1 Q3`.

**Q4. AGE ↔ PostgreSQL consistency.** Tension between "AGE is downstream of
relational" (§13) and "AGE is the live ontology" (§14). Recommendation: add
`concepts` and `concept_edges` relational tables shadowing the AGE ontology.
Wrap PG and AGE writes in a single transaction (AGE Cypher runs in PG and is
naturally transactional). Add `graph rebuild` and `graph verify` commands.
See `arch.md §1 Q4`.

**Q5. Dead-letter drain on small machines.** Tier 4 escalations accumulate on
`gpu_mid` machines that cannot run DeepSeek-R1:70b. Recommendation: document
the `tier4-cloud` escape hatch (RunPod / Lambda Labs spot rental triggered
when queue exceeds 100). Reject silent quality degradation by lower-tier
substitution. See `arch.md §1 Q5`.

**Q6. Cross-source deduplication blocking strategy.** Same conference appears
in WikiCFP, CFP emails, ai-deadlines.yml. Recommendation: per-record ANN top-5
blocking + acronym-year blocking as a sanity check during nightly sweep. Merge
policy: keep highest-confidence row, copy non-null fields from loser, mark
loser `superseded_by` (do not delete). See `arch.md §1 Q6`.

**Q7. Crawl throughput vs politeness.** Estimated first-run cost: ~5,680
WikiCFP pages × 8 s mean delay ≈ 12.6 hours. Steady-state: ~30 min. Per-domain
rate limit insufficient for shared-CDN external sites. Recommendation: keep
Gaussian(8, 2.5) on first run; add per-CIDR /24 secondary limiter for Tier 3
external scraping. See `arch.md §1 Q7`.

**Q8. pgvector index strategy.** Recommendation: IVFFlat for the realistic
project ceiling (50k–200k events). HNSW only if interactive query p95 latency
exceeds 100 ms. Rebuild with `lists = floor(sqrt(N))` after every doubling.
See `arch.md §1 Q8`.

**Q9. Kubernetes vs Docker Compose.** Recommendation: write K8s manifests
alongside Compose from v1.0; ship Compose as the default; activate K8s when
multi-source parallel or scheduled-cloud-runs become needs. See `arch.md §1
Q9` and §5.

**Q10. Ollama model storage.** **RESOLVED (2026-04-29).** Named volume `ollama_models:/root/.ollama` added to `docker-compose.yml` for local Compose. GKE pre-baked profile images deferred to v2.
See `arch.md §1 Q10`.

**Q11. Redis durability.** `cfp:cursor:{source}` and `cfp:dead` are
operational by name but business-critical in practice. Recommendation: enable
AOF persistence (`--appendonly yes`); mirror cursor to `sites.last_cursor` and
dead-letter audit rows to PG. See `arch.md §1 Q11`.

**Q12. LLM JSON-mode failure recovery.** **RESOLVED (2026-04-29).** Local JSON repair → one same-tier retry → escalate. `JSON_RETRY_SAME_TIER = 1`, `JSON_REPAIR_ENABLED = True` in `config.py`. Track `cfp:metrics:parse_fail:{model}`.
See `arch.md §1 Q12`.

**Q13. Cypher query cost ceiling.** Unbounded `*0..` traversals slow as
ontology deepens and `CLASSIFIED_AS` edges grow. Recommendation: cap depth
at `*0..6` defensively now; materialise `concept_descendants` transitive
closure when graph exceeds 5k Concept nodes. See `arch.md §1 Q13`.

**Q14. Quantisation policy.** **RESOLVED (2026-04-29).** Pinned per-profile quant tags in `PROFILE_MODELS` in `codegen/01` (q4_K_M for gpu_small/gpu_mid/gpu_large, q8_0 for dgx).
See `arch.md §1 Q14`.

**Q15. Workshop entity modelling.** `Workshop` graph node vs `is_workshop`
flag is ambiguous. **RESOLVED (2026-04-26).** Use `is_workshop: bool` flag on
Event dataclass and `events.is_workshop` column. Standalone `Workshop` graph
node label is dropped in v1. Can be reinstated in v2 if co-located workshop
queries need separate node traversal.
See `arch.md §1 Q15`.

---

## 20. Known Risks

Brief register; full mitigations and ownership in `arch.md §2`.

| # | Risk | Severity | Mitigation pointer |
|---|------|----------|--------------------|
| R1 | Apache AGE extension breaks across PG version upgrades | High | Pin image digest; maintain rebuild-from-relational script (`arch.md §2 R1`) |
| R2 | WikiCFP IP block on first-run aggressive crawl | High | Keep Gaussian(8, 2.5); contactable User-Agent; honour robots.txt (`arch.md §2 R2`) |
| R4 | GCS sync corruption (partial pg_dump upload) | High | Two-phase upload via staging key + `pg_restore --list` validation; bucket versioning N=7 (`arch.md §2 R4`) |
| R5 | Spot GPU node preempted mid-run | High | Inflight TTL lease; idempotent `INSERT ... ON CONFLICT`; checkpoint Tier 4 every 10 records (`arch.md §2 R5`) |
| R7 | Dead-letter accumulation on machines without DGX | High | `tier4-cloud` escape hatch; queue-depth alerting (`arch.md §2 R7`) |
| R9 | Predatory or spam conferences pollute reports | High | New `PROMPT_QUALITY_GUARD`; static blocklist (`arch.md §2 R9`, `prompts.md`) |
| R10 | Cross-source duplicates pollute reports | High | ANN+acronym blocking; nightly sweep (`arch.md §2 R10`) |
| R13 | Cost overrun: GKE GPU node fails to scale down | High | K8s Job (auto-terminates); `min-nodes=0`; budget alert at $50/day (`arch.md §2 R13`) |
| R14 | GCS credentials leaked in env vars or images | High | Workload Identity in K8s; ADC OAuth on laptops; pre-commit detect-secrets hook (`arch.md §2 R14`) |
