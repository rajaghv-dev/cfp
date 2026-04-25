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

## 3. Model Roster (Ollama)

| Model              | Host     | VRAM peak | Used for                                             |
|--------------------|----------|-----------|------------------------------------------------------|
| `qwen3:4b`         | RTX 3080 | ~3 GB     | Tier 1 triage (real CFP? virtual? rough category?)   |
| `qwen3:14b`        | RTX 3080 | ~10 GB    | Tier 2 structured extraction                         |
| `qwen3:32b`        | RTX 4090 | ~22 GB    | Tier 3 ambiguous classify + tool calling on unknowns |
| `mistral-nemo:12b` | RTX 3080 | ~9 GB     | Long-context (>32k tokens) HTML extraction           |
| `deepseek-r1:32b`  | RTX 4090 | ~22 GB    | Dedup pair reasoning (no tool calling needed)        |
| `deepseek-r1:70b`  | DGX      | ~80 GB    | Tier 4 overnight batch                               |
| `nomic-embed-text` | RTX 3080 | ~300 MB   | 768-d embeddings for semantic dedup                  |

**Why Qwen3 for tool calling**: only family with reliable out-of-the-box tool calling in Ollama
(April 2025). DeepSeek-R1 distills need the MFDoom modelfile; Mistral-NeMo has OpenAI-format
inconsistencies; Phi-4-reasoning does NOT support tools. Use DeepSeek-R1 only for pure reasoning.

---

## 4. Module Layout

```
wiki-cfp/
├── config.py                   # constants, hosts, paths, thresholds
├── prompts.md                  # data: categories, keywords, URLs, parsers, LLM prompts
├── context.md                  # this file
├── docker-compose.yml          # Qdrant + Redis
├── requirements.txt
├── wcfp/
│   ├── __init__.py
│   ├── models.py               # dataclasses: Event, Series, ScrapeJob, TierResult,
│   │                           #   EscalationPayload, OntologyEdge + Category/Tier/JobStatus enums
│   ├── prompts_parser.py       # parse_prompts_md(path) -> ParsedPrompts
│   ├── db.py                   # DuckDB: get_conn, init_schema, upsert_event, iter_events
│   ├── queue.py                # Redis: priority queue, rate limiter, seen-URL cache
│   ├── vectors.py              # Qdrant: ensure_collection, upsert_event_vector, search_similar
│   ├── embed.py                # nomic-embed-text via Ollama -> list[float]
│   ├── fetch.py                # requests session, robots.txt, human_delay, with_retry
│   ├── parsers/
│   │   ├── __init__.py         # KNOWN_PARSERS dict, dispatch(domain, html)
│   │   ├── wikicfp.py          # parse_search_results, parse_event_detail, parse_series_index
│   │   ├── ieee.py             # parse(html) -> Event
│   │   ├── acm.py              # parse(html) -> Event
│   │   ├── springer.py         # parse(html) -> Event
│   │   └── usenix.py           # parse(html) -> Event
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py           # OllamaClient with chat() and chat_with_tools()
│   │   ├── tools.py            # TOOLS list (JSON schema) + closure-bound implementations
│   │   ├── tier1.py            # run_tier1(event: Event) -> TierResult
│   │   ├── tier2.py            # run_tier2(event: Event) -> TierResult
│   │   ├── tier3.py            # run_tier3(event: Event, html: str | None) -> TierResult
│   │   └── tier4.py            # run_tier4_batch(payloads: list[EscalationPayload]) -> list[TierResult]
│   ├── dedup.py                # candidate_pairs(event) + confirm_pair(a,b) via deepseek-r1:32b
│   ├── ontology.py             # owlready2 + rdflib: build_ontology() -> onto
│   ├── pipeline.py             # orchestrator: dequeue -> fetch -> parse -> tiers -> persist
│   └── cli.py                  # python -m wcfp <command>
├── generate_md.py              # reads DuckDB, writes reports/*.md
├── reports/
├── data/
│   ├── wikicfp.duckdb
│   └── archive/                # Parquet snapshots
└── ontology/
    ├── conference_domain.owl
    ├── concepts.json
    ├── edges.json
    └── by_conference.json
```

**Import graph (no cycles):**
```
cli -> pipeline -> fetch, queue, parsers/*, llm/tier*, db, vectors, dedup
llm/tier* -> llm/client, llm/tools, prompts_parser, models, config
parsers/* -> models, config
db, queue, vectors, embed -> models, config
ontology -> db, models, config
generate_md -> db, models, config
```
`models.py` and `config.py` import nothing project-internal.

---

## 5. Data Models (`wcfp/models.py`)

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional


class Category(str, Enum):
    AI               = "AI"
    ML               = "ML"
    DEVOPS           = "DevOps"
    LINUX            = "Linux"
    CHIP_DESIGN      = "ChipDesign"
    MATH             = "Math"
    LEGAL            = "Legal"
    COMPUTER_SCIENCE = "ComputerScience"
    SECURITY         = "Security"
    DATA             = "Data"
    NETWORKING       = "Networking"
    ROBOTICS         = "Robotics"
    BIOINFORMATICS   = "Bioinformatics"


class Tier(int, Enum):
    T1 = 1; T2 = 2; T3 = 3; T4 = 4


class JobStatus(str, Enum):
    PENDING = "pending"; IN_FLIGHT = "in_flight"; DONE = "done"
    FAILED  = "failed";  DEAD      = "dead"


@dataclass(slots=True)
class Event:
    event_id:     int                         # WikiCFP eventid=N — primary key
    acronym:      str
    name:         str
    series_id:    Optional[int]  = None
    edition_year: Optional[int]  = None
    categories:   list[Category] = field(default_factory=list)
    is_virtual:   bool           = False
    when_raw:     Optional[str]  = None
    start_date:   Optional[date] = None
    end_date:     Optional[date] = None
    where_raw:    Optional[str]  = None
    country:      Optional[str]  = None       # ISO-3166 alpha-2 e.g. "IN","US"
    region:       Optional[str]  = None       # "Asia"|"Europe"|"NorthAmerica"|etc.
    india_state:  Optional[str]  = None       # only when country == "IN"
    deadline:     Optional[date] = None
    notification: Optional[date] = None
    camera_ready: Optional[date] = None
    wikicfp_url:  Optional[str]  = None
    official_url: Optional[str]  = None
    raw_tags:     list[str]      = field(default_factory=list)
    scraped_at:   datetime       = field(default_factory=lambda: datetime.now(timezone.utc))
    last_checked: datetime       = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class Series:
    series_id: int; acronym: str
    full_name: Optional[str] = None; wikicfp_url: Optional[str] = None


@dataclass(slots=True)
class ScrapeJob:
    url: str; domain: str
    priority: int          # 1=wikicfp search, 2=series, 3=event, 4=ext-current, 5=ext-archive
    source_event_id: Optional[int] = None
    attempts: int = 0; status: JobStatus = JobStatus.PENDING
    added_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_error: Optional[str] = None


@dataclass(slots=True)
class TierResult:
    tier: Tier; model: str; confidence: float; output: dict
    escalate: bool = False
    escalate_reason: Optional[str] = None
    # values: "low_confidence"|"multi_category"|"unknown_site"|
    #         "long_context"|"dedup_ambiguous"|"ontology_edge"
    elapsed_ms: int = 0


@dataclass(slots=True)
class EscalationPayload:
    record: dict; tier_results: list[TierResult]; escalate_reason: str
    raw_html: Optional[str] = None   # only when escalate_reason == "unknown_site"


@dataclass(slots=True)
class OntologyEdge:
    subject: str; predicate: str; object: str; confidence: float
    # predicate: "is_a"|"part_of"|"related_to"|"synonym_of"
    source_event_id: Optional[int] = None
```

---

## 6. Configuration (`config.py`)

```python
from pathlib import Path
import os

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"; REPORTS_DIR = ROOT / "reports"
ONTOLOGY_DIR = ROOT / "ontology"; DUCKDB_PATH = DATA_DIR / "wikicfp.duckdb"

OLLAMA_HOSTS = {
    "rtx4090": os.getenv("OLLAMA_RTX4090", "http://10.0.0.10:11434"),
    "rtx3080": os.getenv("OLLAMA_RTX3080", "http://10.0.0.11:11434"),
    "dgx":     os.getenv("OLLAMA_DGX",     "http://10.0.0.12:11434"),
}

MODEL_HOST = {
    "qwen3:4b": "rtx3080", "qwen3:14b": "rtx3080",
    "mistral-nemo:12b": "rtx3080", "nomic-embed-text": "rtx3080",
    "qwen3:32b": "rtx4090", "deepseek-r1:32b": "rtx4090",
    "deepseek-r1:70b": "dgx",
}

TIER_THRESHOLD  = {1: 0.85, 2: 0.85, 3: 0.80}
LONG_CONTEXT_TOKENS = 32_000        # tiktoken cl100k_base

REDIS_URL   = os.getenv("REDIS_URL",  "redis://localhost:6379/0")
QDRANT_URL  = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = "wikicfp_events"
EMBED_DIM   = 768

DEDUP_COSINE_THRESHOLD = 0.92; DEDUP_TOP_K = 5

USER_AGENT            = "wiki-cfp-scraper/1.0 (+contact@example.com)"
HUMAN_DELAY_MEAN      = 8.0;  HUMAN_DELAY_STD  = 2.5
HUMAN_DELAY_MIN       = 5.0;  HUMAN_DELAY_MAX  = 15.0
HUMAN_DELAY_LONG_PROB = 0.10  # 10% chance of 15-45s "reading" pause

MAX_RETRIES = 5; RETRY_BACKOFF_BASE = 2.0; RETRY_BACKOFF_CAP = 600
DEAD_LETTER_KEY = "wcfp:dead"
PROMPTS_FILE = ROOT / "prompts.md"
```

---

## 7. Storage

### 7.1 DuckDB schema (`wcfp/db.py`)

Public API: `get_conn()`, `init_schema(conn)`, `upsert_event(conn, Event)`,
`upsert_series(conn, Series)`, `iter_events(conn, where=None) -> Iterator[Event]`.

```sql
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY, acronym VARCHAR, name VARCHAR,
    series_id INTEGER, edition_year INTEGER, categories VARCHAR[],
    is_virtual BOOLEAN, when_raw VARCHAR, start_date DATE, end_date DATE,
    where_raw VARCHAR, country VARCHAR, region VARCHAR, india_state VARCHAR,
    deadline DATE, notification DATE, camera_ready DATE,
    wikicfp_url VARCHAR, official_url VARCHAR, raw_tags VARCHAR[],
    scraped_at TIMESTAMP, last_checked TIMESTAMP
);
CREATE TABLE IF NOT EXISTS series (
    series_id INTEGER PRIMARY KEY, acronym VARCHAR,
    full_name VARCHAR, wikicfp_url VARCHAR
);
CREATE TABLE IF NOT EXISTS sites (
    domain VARCHAR PRIMARY KEY, parser_type VARCHAR,
    last_scraped TIMESTAMP, robots_txt VARCHAR,
    crawl_delay_s INTEGER DEFAULT 5, notes VARCHAR
);
CREATE TABLE IF NOT EXISTS tier_runs (
    event_id INTEGER, tier INTEGER, model VARCHAR, confidence DOUBLE,
    output_json VARCHAR, escalate BOOLEAN, escalate_reason VARCHAR,
    elapsed_ms INTEGER, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id, tier, ts)
);
CREATE INDEX IF NOT EXISTS idx_events_country  ON events(country);
CREATE INDEX IF NOT EXISTS idx_events_state    ON events(india_state);
CREATE INDEX IF NOT EXISTS idx_events_start    ON events(start_date);
CREATE INDEX IF NOT EXISTS idx_events_deadline ON events(deadline);
```

Upserts: `INSERT OR REPLACE` / `ON CONFLICT (event_id) DO UPDATE SET ...`

### 7.2 Qdrant (`wcfp/vectors.py`)

Collection: `wikicfp_events`, vectors: size=768, distance=COSINE.
Embedding text: `f"{acronym} {name} {start_date or ''}".lower()`.

```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
# ensure_collection / upsert_event_vector / search_similar
# — see §4 for full signatures
```

### 7.3 Redis key schema (`wcfp/queue.py`)

| Key pattern                 | Type       | TTL           | Purpose                                  |
|-----------------------------|------------|---------------|------------------------------------------|
| `wcfp:queue`                | sorted set | none          | priority queue (score = priority*1e10+ms)|
| `wcfp:inflight:{job_id}`    | string     | 600 s         | in-flight lease; expiry = re-enqueue     |
| `wcfp:seen:{sha1(url)}`     | string "1" | 30 days       | SETNX before enqueue                     |
| `wcfp:rate:{domain}`        | string "1" | crawl_delay_s | SETNX rate limiter                       |
| `wcfp:robots:{domain}`      | string     | 1 day         | cached robots.txt                        |
| `wcfp:dead`                 | list       | none          | RPUSH after MAX_RETRIES                  |
| `wcfp:metrics:tier{N}`      | hash       | none          | ok / escalated / failed counters         |
| `wcfp:cursor:{source}`      | string     | none          | resume cursor per source                 |

---

## 8. Ollama tool calling (`wcfp/llm/client.py`)

`pip install ollama`. Routes via `MODEL_HOST[model] -> OLLAMA_HOSTS[host]`.

```python
from ollama import Client
from config import OLLAMA_HOSTS, MODEL_HOST
import json
from typing import Any, Callable, Optional

class OllamaClient:
    def __init__(self):
        self._clients = {n: Client(host=u, timeout=300)
                         for n, u in OLLAMA_HOSTS.items()}

    def _client_for(self, model: str) -> Client:
        return self._clients[MODEL_HOST[model]]

    def chat(self, model, messages, tools=None, format=None, options=None) -> dict:
        kw: dict[str, Any] = {"model": model, "messages": messages}
        if tools:   kw["tools"]   = tools
        if format:  kw["format"]  = format
        if options: kw["options"] = options
        return self._client_for(model).chat(**kw)

    def chat_with_tools(self, model: str, system: str, user: str,
                        tools: list[dict],
                        tool_impls: dict[str, Callable[..., Any]],
                        max_iters: int = 6) -> dict:
        messages = [{"role": "system", "content": system},
                    {"role": "user",   "content": user}]
        for _ in range(max_iters):
            resp  = self.chat(model=model, messages=messages, tools=tools)
            msg   = resp["message"]
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                return resp
            for call in calls:
                name = call["function"]["name"]
                args = call["function"]["arguments"]
                if isinstance(args, str): args = json.loads(args)
                try:    result = tool_impls[name](**args)
                except Exception as e: result = {"error": repr(e)}
                messages.append({"role": "tool", "name": name,
                                  "content": json.dumps(result, default=str)})
        raise RuntimeError(f"tool loop exceeded {max_iters} iterations")
```

Tools schema in `wcfp/llm/tools.py` — `TOOLS` list with:
`extract_text(selector)`, `find_links(pattern)`, `get_field(label)`,
`is_conference_page()`, `classify_category(text)`, `detect_virtual(text)`.

---

## 9. Parsing strategy

**Rule-based** (no GPU): `wcfp/parsers/wikicfp.py`
- `parse_search_results(html) -> list[Event]` — paired TR rows.
- `parse_event_detail(html) -> Event` — TD: `When|date|Where|city|Submission Deadline|date`.
- `parse_series_index(html) -> list[Series]` — `<a href="/cfp/program?id=N&s=ACRONYM">`.

**Hybrid dispatch per URL:**
```
domain in KNOWN_PARSERS              → rule-based (no GPU)
unknown domain:
  tiktoken(html) <= 32_000           → qwen3:32b + tools  (RTX 4090)
  tiktoken(html)  > 32_000           → mistral-nemo:12b   (RTX 3080)
  still unresolved after tier 3      → DGX overnight queue
```

Additional `PARSER:` lines in `prompts.md` extend `KNOWN_PARSERS` at startup.

---

## 10. Tiered curation

```
Raw records
    │
    ▼ TIER 1  qwen3:4b  RTX 3080  ~200 rec/min
  Output JSON: {is_cfp, categories:[str], is_virtual, confidence}
  conf >= 0.85 → DuckDB. conf < 0.85 → escalate
    │ ~20%
    ▼ TIER 2  qwen3:14b  RTX 3080
  Output JSON: full Event-shaped dict + confidence
  conf >= 0.85 → DuckDB. conf < 0.85 → escalate
    │ ~5%
    ▼ TIER 3  qwen3:32b  RTX 4090  (tool calling for unknown_site)
  Output JSON: Event + archive_urls + tool_trace + confidence
  conf >= 0.80 → DuckDB. conf < 0.80 → escalate
    │ ~1%
    ▼ TIER 4  deepseek-r1:70b  DGX  (overnight — no tool calling)
  Output JSON: final Event + ontology:[OntologyEdge] + dedup:{same,reason}
  Always final.
```

Escalation JSON between tiers:
```json
{"record": {...}, "tier_results": [...],
 "escalate_reason": "low_confidence|multi_category|unknown_site|long_context|dedup_ambiguous|ontology_edge",
 "raw_html": "...only for unknown_site..."}
```

Prompts live in `prompts.md` as `PROMPT_TIER1`…`PROMPT_TIER4`.

---

## 11. Deduplication

```python
# wcfp/dedup.py
def candidate_pairs(event): ...   # embed -> Qdrant ANN -> filter by DEDUP_COSINE_THRESHOLD
def confirm_pair(a, b) -> bool:   # deepseek-r1:32b, format="json", returns {"same": bool}
```

---

## 12. Error handling (`wcfp/fetch.py`)

```python
def with_retry(fn, *args, job: ScrapeJob, **kw):
    for attempt in range(1, MAX_RETRIES + 1):
        try: return fn(*args, **kw)
        except RetryableError as e:
            if attempt >= MAX_RETRIES:
                redis.rpush(DEAD_LETTER_KEY, json.dumps(asdict(job), default=str))
                job.status = JobStatus.DEAD; raise
            time.sleep(min(RETRY_BACKOFF_CAP, RETRY_BACKOFF_BASE**attempt) + random.random())
        except FatalError:
            redis.rpush(DEAD_LETTER_KEY, json.dumps(asdict(job), default=str))
            job.status = JobStatus.DEAD; raise
```

- `RetryableError`: HTTP 429/5xx, timeout, LLM JSON decode error.
- `FatalError`: HTTP 404/410, robots.txt disallow, parser hard-reject.

---

## 13. `prompts.md` parser spec (`wcfp/prompts_parser.py`)

Accepted line types (anything else raises `ValueError` with line number):
```
# / blank / ## heading   → ignored
CATEGORY: <enum value>   → opens category block
KEYWORD:  <query text>   → belongs to current CATEGORY
URL:      <http://...>   → belongs to current CATEGORY or INDEX block
INDEX_SERIES:  <A-Z>     → opens series-index block; next URL closes it
INDEX_JOURNAL: <A-Z>     → opens journal-index block; next URL closes it
PARSER: <domain> -> <module.path>
PROMPT_TIER1: |          → multi-line body, 2-space indent, ends at next key or EOF
PROMPT_TIER2..4: |
PROMPT_DEDUP: |
PROMPT_ONTOLOGY_SYNONYM: |
PROMPT_ONTOLOGY_ISA: |
```

Returns `ParsedPrompts(categories, indexes, parsers, prompts)`.
Full reference implementation: see `wcfp/prompts_parser.py` in repo.

---

## 14. `generate_md.py` upgrade path

1. Replace JSON load → `duckdb.connect(DUCKDB_PATH, read_only=True)` query.
2. Use SQL GROUP BY for country, india_state aggregations.
3. Emit: `reports/<category>.md`, `reports/by_date.md`, `reports/by_deadline.md`,
   `reports/regions/<country>.md`, `reports/india/<state>.md`.
4. Each row links to both `wikicfp_url` and `official_url`.
5. Keep `generate_all(out_dir: Path)` signature unchanged.

---

## 15. Ontology learning (`wcfp/ontology.py`)

`owlready2>=0.46` + `rdflib>=7.0` installed ✓. Visualise in Protégé (free, Java).

**Hierarchy root**: `ConferenceDomain`
```
ConferenceDomain
├── ResearchField
│   ├── AI -> MachineLearning (DeepLearning, RL, NLP), ComputerVision
│   ├── Systems -> OperatingSystems(Linux), DevOps, Networking
│   ├── HardwareDesign(ChipDesign) -> VLSI, EDA, FPGA
│   ├── Mathematics, Legal
├── ConferenceType { Conference, Workshop, Symposium, Journal }
├── Organization   { IEEE, ACM, Springer, USENIX, Independent }
└── Location -> Virtual | Physical -> Country -> Region -> City
                                      India    -> State  -> City
```

Tier responsibilities: T1=extract raw_tags, T2=synonym clustering, T3=is_a inference, T4=validate+new branches.
Prompts: `PROMPT_ONTOLOGY_SYNONYM`, `PROMPT_ONTOLOGY_ISA` in `prompts.md`.
Outputs: `ontology/{conference_domain.owl, concepts.json, edges.json, by_conference.json}`.

---

## 16. Installation

`requirements.txt`:
```
duckdb>=1.0   qdrant-client>=1.9   redis>=5.0    ollama>=0.3
beautifulsoup4>=4.12   lxml>=5.0   requests>=2.32
owlready2>=0.46   rdflib>=7.0   tiktoken>=0.7
```

`docker-compose.yml` (Qdrant + Redis):
```yaml
version: "3.9"
services:
  qdrant:
    image: qdrant/qdrant:v1.9.2
    ports: ["6333:6333","6334:6334"]
    volumes: ["./data/qdrant:/qdrant/storage"]
    restart: unless-stopped
  redis:
    image: redis:7-alpine
    command: ["redis-server","--appendonly","yes"]
    ports: ["6379:6379"]
    volumes: ["./data/redis:/data"]
    restart: unless-stopped
```

```bash
docker compose up -d
python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
python -m wcfp.cli init-db && python -m wcfp.cli enqueue-seeds && python -m wcfp.cli run-pipeline
```

---

## 17. CLI (`python -m wcfp <command>`)

| Command            | Action                                                       |
|--------------------|--------------------------------------------------------------|
| `init-db`          | Create DuckDB schema + Qdrant collection                     |
| `enqueue-seeds`    | Parse `prompts.md`, push all search/index URLs to Redis      |
| `run-pipeline`     | Long-running worker: dequeue->fetch->parse->tiers->save      |
| `tier4-batch`      | Drain `wcfp:dead`; run deepseek-r1:70b on DGX overnight      |
| `dedup-sweep`      | Rebuild Qdrant vectors; run pairwise dedup                   |
| `build-ontology`   | Emit OWL + JSON to `ontology/`                               |
| `generate-reports` | Run `generate_md.generate_all(REPORTS_DIR)`                  |
| `replay-dead`      | Pop N items from `wcfp:dead` and re-enqueue                  |

---

## 18. Key Decisions Log

| Date       | Decision                               | Reason                                                        |
|------------|----------------------------------------|---------------------------------------------------------------|
| 2026-04-25 | Python + requests + BS4                | No WikiCFP API exists                                         |
| 2026-04-25 | 5s+ human-like random delays           | WikiCFP guideline; avoid rate-bans                            |
| 2026-04-25 | event_id as primary dedup key          | WikiCFP eventid=N is globally unique per edition              |
| 2026-04-25 | Qwen3 for all tool calling             | Only local family with reliable Ollama tool-call support      |
| 2026-04-25 | DeepSeek-R1 for pure reasoning         | Best accuracy; no tool-call overhead for yes/no dedup         |
| 2026-04-25 | DuckDB over PostgreSQL                 | Single-machine, read-heavy, zero server overhead              |
| 2026-04-25 | Qdrant for vectors                     | Persistent, filterable; better than ChromaDB/FAISS here       |
| 2026-04-25 | Redis sorted set for queue             | Priority + per-domain TTL rate limiting is native             |
| 2026-04-25 | Series A–Z index in prompts.md         | Covers ~3000+ known conference series                         |
| 2026-04-25 | mistral-nemo:12b for long pages        | 128k context; others truncate at 32k                          |
| 2026-04-25 | india.md state-wise, 80+ city map      | User requirement                                              |
| 2026-04-25 | 4-tier pipeline (4b->14b->32b->70b)   | ~80% resolved cheaply; DGX for the hard 1%                   |
| 2026-04-25 | owlready2 + rdflib for ontology        | Standard toolchain; Protégé-compatible OWL                    |
| 2026-04-25 | tiktoken for token-count routing       | Fast, accurate, no GPU                                        |

---

## 19. Portable Clone & State Restore

Any machine should be able to:
1. `git clone` the repo
2. Run `bash setup.sh` — pulls DB state, downloads missing Ollama models, installs deps
3. Continue from the last saved scrape state
4. On finish: push DB + data back to remote storage

### Remote state storage options (in order of preference)

| Option               | Tool          | Free tier | Notes                                      |
|----------------------|---------------|-----------|--------------------------------------------|
| Google Cloud Storage | `gcloud` CLI  | 5 GB free | Simple bucket; works from any machine      |
| Backblaze B2         | `rclone`      | 10 GB free| Cheapest; S3-compatible                    |
| AWS S3               | `aws` CLI     | 5 GB free | Most universal; widely supported           |
| GitHub LFS           | `git lfs`     | 1 GB free | DuckDB can exceed 1 GB; not recommended    |
| Self-hosted MinIO    | `rclone`/S3   | unlimited | Best if DGX has a static IP                |

**Recommended: Backblaze B2 + rclone** — cheapest at scale, S3-compatible,
works with `rclone sync` from any machine.

### State files to sync (upload after run, download before run)

```
data/wikicfp.duckdb          # primary DB — always sync
data/qdrant/                 # Qdrant vectors — sync if present
data/archive/                # Parquet snapshots — sync
ontology/                    # OWL + JSON — sync
```

### `setup.sh` contract (to be implemented)

```bash
bash setup.sh [--skip-models] [--skip-db-pull] [--storage gcs|b2|s3|minio]
```

Steps performed:
1. Check Python 3.10+; create `.venv`; `pip install -r requirements.txt`
2. `docker compose up -d` (Qdrant + Redis)
3. Pull DB state from remote storage into `data/` (skip if `--skip-db-pull`)
4. Check each model in `MODEL_HOST`; `ollama pull <model>` if missing (skip if `--skip-models`)
5. Verify WikiCFP connectivity
6. Write last-setup timestamp to `README.md`

After a run completes, `pipeline.py` calls `sync_state(direction="push")`
which uploads changed files back to remote storage.

### `wcfp/sync.py` interface

```python
def pull_state(storage: str = "b2") -> None:
    """Download DB + vectors + ontology from remote before starting a run."""

def push_state(storage: str = "b2") -> None:
    """Upload DB + vectors + ontology to remote after a run completes."""

def get_rclone_remote(storage: str) -> str:
    """Return rclone remote name: 'b2:wikicfp-state', 'gcs:wikicfp-state', etc."""
```

Storage backend is selected from env var `WCFP_STORAGE` (default: `b2`).
Remote bucket/path configured in `rclone.conf` (not committed; per-machine).

### Ollama model check in `setup.sh`

```bash
MODELS_NEEDED="qwen3:4b qwen3:14b qwen3:32b mistral-nemo:12b deepseek-r1:32b deepseek-r1:70b nomic-embed-text"
for model in $MODELS_NEEDED; do
    if ! ollama list | grep -q "^$model"; then
        echo "Pulling $model ..."
        ollama pull "$model"
    fi
done
```

On the RTX 3080 machine only pull: `qwen3:4b qwen3:14b mistral-nemo:12b nomic-embed-text`
On the RTX 4090 machine only pull: `qwen3:32b deepseek-r1:32b`
On DGX only pull: `deepseek-r1:70b`

Controlled via env var `WCFP_MACHINE=rtx3080|rtx4090|dgx` — `setup.sh` reads it and
only pulls the models relevant for that machine.

### GitHub push after run

After each pipeline run, `pipeline.py` also commits updated reports and
`data/latest.json` back to the repo and pushes:

```bash
git add reports/ data/latest.json ontology/
git commit -m "auto: scrape run $(date +%Y-%m-%d)"
git push origin main
```

Requires a deploy key or GITHUB_TOKEN in the environment.
Heavy state files (DuckDB, Qdrant) go to rclone remote, NOT git.
