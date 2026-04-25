# Project Context — WikiCFP Conference Scraper

> Living document. Updated after every major design decision.
> Last updated: 2026-04-25

---

## 1. Project Goal

Build a fully automated pipeline that:
1. Scrapes WikiCFP (keyword search + full A–Z series index)
2. Follows links to official conference websites and previous-year archives
3. Deduplicates across all sources using event ID + semantic embeddings
4. Classifies every conference into one or more categories
5. Generates organised Markdown reports (by category, date, region, India state-wise)
6. Refreshes automatically so past conferences move out of the upcoming section

---

## 2. Hardware

| Machine | GPU | VRAM | Role |
|---|---|---|---|
| Workstation A | RTX 4090 | 24 GB | Primary inference — classification, tool calling |
| Workstation B | RTX 3080 | 16 GB | Secondary — extraction, dedup confirmation |
| DGX Station | ~8× A100 | 256 GB | Heavy reasoning, large-model runs, batch jobs |

---

## 3. Model Decisions

### Tool Calling / HTML Parsing

**Winner: Qwen3 (all sizes)** — only model family with fully reliable, out-of-the-box
tool calling in Ollama as of April 2025. DeepSeek-R1 distills require custom chat
templates (MFDoom variant). Mistral-NeMo is trained for tools but has OpenAI-format
inconsistencies in Ollama's API layer. Phi-4-reasoning does NOT support tool calling.

### Hardware → Model Assignment

| Task | Model | Runs on | Why |
|---|---|---|---|
| Classify category (fast) | `qwen3:8b` | RTX 3080 | Fits in 5 GB, thinking mode, tool calling |
| Classify ambiguous / overlapping | `qwen3:32b` | RTX 4090 | 32B reasoning catches edge cases |
| Extract fields from short CFP pages | `qwen3:14b` | RTX 3080 | 32k ctx sufficient, tool calling |
| Extract fields from long CFP pages | `mistral-nemo:12b` | RTX 3080 | 128k context, handles full HTML dumps |
| Dedup confirmation (pair reasoning) | `deepseek-r1:32b` | RTX 4090 | Best pure reasoning for yes/no dedup |
| Complex multi-site reasoning | `qwen3:72b` | DGX | Large reasoning for unknown site layouts |
| Batch overnight classification | `deepseek-r1:70b` | DGX | High accuracy, no time pressure |
| Semantic candidate pairing | `nomic-embed-text` | Any CPU | 274 MB, fast cosine-sim |

### Why Not Phi-4-reasoning for Tool Calling
Phi-4-reasoning-plus explicitly does not support tool calling. Phi-4-mini does, but
at 3.8B it lacks the semantic depth for academic domain classification. Skip it here.

### Why Qwen3 over DeepSeek-R1 for Tool Calling
DeepSeek-R1 distills need the MFDoom custom modelfile to enable tool calling — extra
setup, not upstream-maintained. Qwen3 works out of the box with Ollama's `/api/chat`
`tools` parameter. Use DeepSeek-R1:32B/70B on DGX for pure reasoning tasks where
tool calling isn't needed (e.g., overnight dedup batch).

---

## 4. Parsing Strategy

### Rule-based (fast, no GPU)
Use BeautifulSoup + lxml for **WikiCFP pages** — structure is consistent and known:
- Search results: paired rows (acronym+link row, then when/where/deadline row)
- Event detail: single TD with `When|date|Where|city|Submission Deadline|date` pattern
- Series index: `<a href="/cfp/program?id=N&s=ACRONYM">` links per letter

### LLM Tool Calling (for unknown external sites)
When the scraper follows a link to an external conference website (e.g. neurips.cc,
icml.cc, acl2026.org) the page structure is unknown. Use Qwen3 with these tools:

```python
tools = [
    extract_text(selector: str) -> str,
    find_links(pattern: str) -> list[str],
    get_field(label: str) -> str,          # e.g. "submission deadline"
    is_conference_page() -> bool,
    classify_category(text: str) -> list[str],
    detect_virtual(text: str) -> bool,
]
```

Qwen3:32B (RTX 4090) handles unknown sites in real-time.
Qwen3:72B (DGX) used for batch overnight processing of complex sites.

### Hybrid pipeline per URL
```
URL arrives in queue
  ├── domain == wikicfp.com  →  rule-based parser  (fast, no GPU)
  └── domain != wikicfp.com
        ├── domain in KNOWN_PARSERS  →  dedicated parser module
        └── domain unknown
              ├── page < 32k tokens   →  qwen3:32b tool calling (RTX 4090)
              └── page > 32k tokens   →  mistral-nemo:12b extraction (RTX 3080)
```

---

## 5. Database Stack

### Chosen: DuckDB + Qdrant + Redis (all self-hosted, all free)

**DuckDB** — primary analytical store
- Embedded, file-based, zero server overhead
- Native JSON support, columnar = fast analytics queries
- Handles Parquet for archiving
- `pip install duckdb`
- Schema: `events`, `series`, `scrape_queue`, `sites`

**Qdrant** — vector store for semantic deduplication
- Self-hosted via Docker: `docker run -p 6333:6333 qdrant/qdrant`
- Stores `nomic-embed-text` embeddings of `acronym + name + when`
- Cosine similarity threshold 0.92 → candidate duplicate pairs
- Qwen3:32B confirms or rejects each pair
- `pip install qdrant-client`

**Redis** — scrape queue + rate limiter + seen-URL cache
- Self-hosted: `docker run -p 6379:6379 redis:alpine`
- Sorted set as priority queue (WikiCFP > series pages > event details > external)
- Per-domain rate limiting: SET with TTL enforces min delay between requests
- SETNX as seen-URL dedup (fast O(1) check before fetching)
- `pip install redis`

**Why not PostgreSQL?**
Single-machine workflow — DuckDB + SQLite is simpler and faster for read-heavy
analytics. Switch to PostgreSQL only if multi-machine concurrent writes are needed.

**Why not ChromaDB / FAISS?**
Qdrant has persistent storage, filtering, and payload support built-in. ChromaDB is
in-process only. FAISS requires manual persistence. Qdrant is the right tier here.

### DuckDB Schema (planned)

```sql
-- Canonical event record (one row per unique WikiCFP event ID)
CREATE TABLE events (
    event_id        INTEGER PRIMARY KEY,   -- from wikicfp eventid=N
    acronym         VARCHAR,
    name            VARCHAR,
    series_id       INTEGER,               -- FK → series
    edition_year    INTEGER,
    category        VARCHAR[],             -- ['AI','ML']
    is_virtual      BOOLEAN,
    when_raw        VARCHAR,
    start_date      DATE,
    end_date        DATE,
    where_raw       VARCHAR,
    country         VARCHAR,
    region          VARCHAR,               -- USA/Europe/India/etc.
    india_state     VARCHAR,
    deadline        DATE,
    notification    DATE,
    camera_ready    DATE,
    wikicfp_url     VARCHAR,
    official_url    VARCHAR,               -- extracted from detail page
    scraped_at      TIMESTAMP,
    last_checked    TIMESTAMP
);

-- Conference series (e.g. ICML, NeurIPS, VLSI-DAT)
CREATE TABLE series (
    series_id       INTEGER PRIMARY KEY,   -- from wikicfp program?id=N
    acronym         VARCHAR,
    full_name       VARCHAR,
    wikicfp_url     VARCHAR
);

-- External sites to scrape (found via official_url links)
CREATE TABLE sites (
    domain          VARCHAR PRIMARY KEY,
    parser_type     VARCHAR,               -- 'rule-based' | 'llm-tool' | 'known'
    last_scraped    TIMESTAMP,
    robots_txt      VARCHAR,
    crawl_delay_s   INTEGER DEFAULT 5,
    notes           VARCHAR
);

-- Scrape queue (mirrors Redis, persisted here for resume-after-crash)
CREATE TABLE scrape_queue (
    url             VARCHAR PRIMARY KEY,
    domain          VARCHAR,
    priority        INTEGER,               -- 1=wikicfp 2=series 3=event 4=external
    source_event_id INTEGER,
    added_at        TIMESTAMP,
    attempts        INTEGER DEFAULT 0,
    status          VARCHAR DEFAULT 'pending'  -- pending/done/failed
);
```

---

## 6. Scraping Architecture

### Three-tier source hierarchy

```
Tier 1 — WikiCFP keyword search
  ?conference=<keyword>&page=N
  Discovers event IDs + basic fields

Tier 2 — WikiCFP series index (A–Z)
  /cfp/series?t=c&i=A  through  Z
  Discovers ALL known conference series → program pages
  /cfp/program?id=N  lists every edition (year) of a series
  → feeds Tier 1 event detail pages

Tier 3 — External conference websites
  official_url extracted from each event detail page
  Scraped separately, domain-isolated, LLM-assisted
```

### Previous-years handling
WikiCFP series program pages (`/cfp/program?id=N&s=ACRONYM`) list every historical
edition of a conference. The scraper:
1. Fetches every program page discovered via series index
2. Collects ALL edition links (not just current year)
3. Stores each edition as a separate event row with `edition_year`
4. External conference websites: detect `/2024/`, `/2023/` archive subpages and
   queue them as separate scrape jobs with lower priority (priority=5)

### Human-like crawl timing
```python
import random, time

def human_delay(min_s=5, max_s=15):
    # Gaussian around 8s, clamped to [min_s, max_s]
    delay = random.gauss(8, 2.5)
    delay = max(min_s, min(max_s, delay))
    # 1-in-10 chance of a "reading pause" (15–45s)
    if random.random() < 0.1:
        delay = random.uniform(15, 45)
    time.sleep(delay)
```

### Per-domain isolation
Each external domain runs in its own scrape session:
- Separate Redis rate-limit key per domain
- Separate DuckDB `sites` row tracking crawl_delay and robots.txt
- Never mix domains in the same worker to avoid cross-site rate interference
- Unknown domains default to 10s delay until robots.txt is checked

### Queue priority
```
1  WikiCFP search result pages     (discovery)
2  WikiCFP series/program pages    (historical edition links)
3  WikiCFP event detail pages      (official URL + full fields)
4  External conference websites    (current year)
5  External conference archives    (previous years)
```

---

## 7. Multi-site LLM Workflow

```
for each external URL dequeued:
  1. Fetch HTML (rule-based requests + human delay)
  2. Check domain in KNOWN_PARSERS → use dedicated module if yes
  3. Else: token count HTML
     a. < 32k tokens  → qwen3:32b with tool calling (RTX 4090, real-time)
     b. > 32k tokens  → mistral-nemo:12b full-context dump (RTX 3080)
     c. overnight batch → queue for qwen3:72b on DGX
  4. Tools called by model:
       extract_field("submission deadline")
       extract_field("conference dates")
       extract_field("location")
       detect_virtual()
       find_archive_links()     ← discovers /2024/, /2023/ subpages
       classify_category()
  5. Merge extracted fields into DuckDB events row
  6. Any new archive links → add to queue at priority 5
```

---

## 8. Deduplication Strategy

```
Step 1 — Hard dedup: event_id (WikiCFP eventid=N is unique per edition)
Step 2 — Cross-source soft dedup:
  a. Generate embedding: nomic-embed-text(acronym + name + start_date)
  b. Qdrant ANN search: top-5 candidates within cosine distance 0.08
  c. For each candidate pair → deepseek-r1:32b (RTX 4090):
       "Are these the same conference edition? [A] [B] → YES/NO + reason"
  d. YES → merge records, keep richer field set, mark duplicate
```

---

## 9. Report Generation (existing)

Runs after every scrape. `generate_md.py` reads `data/latest.json` and writes:
- `reports/ai.md`, `ml.md`, `devops.md`, `linux.md`, `chipdesign.md`, `math.md`, `legal.md`
- `reports/by_date.md` — all conferences sorted by start date
- `reports/usa.md`, `europe.md`, `uk.md`, `singapore.md`, `switzerland.md`
- `reports/india.md` — state-wise (city → state mapping for 80+ cities)
- Each report: Upcoming section (soonest first) + Past section (most-recent first)
- Past/upcoming split is live — recalculated on every run against today's date

---

## 10. File Structure

```
wiki-cfp/
├── scraper.py          # WikiCFP search + series scraper (to be rewritten)
├── generate_md.py      # Markdown report generator
├── prompts.md          # All search queries, keywords, series index URLs
├── context.md          # This file — architecture decisions + discussions
├── requirements.txt
├── setup.sh            # One-command environment setup
├── data/
│   ├── latest.json     # Always current full dataset
│   ├── events_cache/   # Per event_id JSON cache (avoid re-fetching)
│   └── conferences_YYYYMMDD_HHMMSS.{json,csv}
├── reports/            # Auto-generated Markdown reports
│   ├── ai.md
│   ├── ml.md
│   ├── by_date.md
│   ├── india.md
│   └── ...
└── db/
    └── wikicfp.duckdb  # Primary analytical store
```

---

## 11. Key Decisions Log

| Date | Decision | Reason |
|---|---|---|
| 2026-04-25 | Python + requests + BS4 for scraper | No API exists; HTML scraping only option |
| 2026-04-25 | 5s+ human-like random delays | WikiCFP community guideline; avoid bans |
| 2026-04-25 | Event ID as primary dedup key | WikiCFP eventid=N is globally unique per edition |
| 2026-04-25 | Qwen3 for tool calling | Only local model with reliable Ollama tool-call support |
| 2026-04-25 | DeepSeek-R1 for pure reasoning (no tool call) | Best reasoning accuracy; use on DGX for batch |
| 2026-04-25 | DuckDB over PostgreSQL | Single-machine, read-heavy analytics, zero server overhead |
| 2026-04-25 | Qdrant for vectors | Persistent, filterable, better than Chroma/FAISS for this scale |
| 2026-04-25 | Redis for queue + rate limiting | Per-domain TTL-based rate limiting is native to Redis |
| 2026-04-25 | Series index A–Z in prompts.md | Comprehensive coverage beyond keyword search |
| 2026-04-25 | Mistral-NeMo:12b for long pages | 128k context; others truncate at 32k |
| 2026-04-25 | india.md state-wise with 80+ city map | User requirement; city→state lookup table |
| 2026-04-25 | Tiered model pipeline (small→medium→large) | Economical: most records resolved by cheap models; only hard cases reach DGX |
| 2026-04-25 | Ontology as learning exercise on this repo | Build ConferenceDomain OWL ontology from WikiCFP category tags using owlready2 + rdflib |

---

## 12. Tiered Curation Pipeline

The key insight: **route by confidence, not by task type.** A small model that is
sure is better than a large model for obvious cases.

```
Raw scraped records
        │
        ▼
┌───────────────────────────────────────────────────────────┐
│ TIER 1 — qwen3:4b on RTX 3080 (~200 rec/min)             │
│  • Is this a real CFP? (discard spam/journals)            │
│  • Does it touch any target category?                     │
│  • Is it virtual?                                         │
│  Confidence ≥ 0.85 → write to DuckDB, done               │
│  Confidence < 0.85 → ESCALATE to Tier 2                  │
└───────────────┬───────────────────────────────────────────┘
                │ ~20% of records
                ▼
┌───────────────────────────────────────────────────────────┐
│ TIER 2 — qwen3:14b on RTX 3080                           │
│  • Multi-label classification                             │
│  • Structured field extraction                            │
│  • Find archive links in external pages                   │
│  Confidence ≥ 0.85 → done                                │
│  Confidence < 0.85 → ESCALATE to Tier 3                  │
└───────────────┬───────────────────────────────────────────┘
                │ ~5% of records
                ▼
┌───────────────────────────────────────────────────────────┐
│ TIER 3 — qwen3:32b on RTX 4090 (tool calling)            │
│  • Overlapping/ambiguous categories                       │
│  • Unknown external site layouts via tool calling         │
│  • Dedup pair confirmation                                │
│  Confidence ≥ 0.80 → done                                │
│  Confidence < 0.80 → ESCALATE to Tier 4 (batch)          │
└───────────────┬───────────────────────────────────────────┘
                │ ~1% of records
                ▼
┌───────────────────────────────────────────────────────────┐
│ TIER 4 — deepseek-r1:70b on DGX (overnight batch)        │
│  • Hardest dedup cases                                    │
│  • Ontology edge inference                                │
│  • Cross-domain relationship detection                    │
│  Always produces a final answer                           │
└───────────────────────────────────────────────────────────┘
```

Escalation payload between tiers:
```json
{
  "record": { ...event fields... },
  "tier1_result": { "categories": ["AI"], "confidence": 0.71 },
  "escalate_reason": "low_confidence | multi_category | unknown_site | dedup_ambiguous"
}
```

---

## 13. Ontology Learning (using this repo as exercise)

This project is a natural sandbox for **ontology engineering** because WikiCFP
already provides raw material: conference names, acronyms, category tags, and
co-occurrence patterns across thousands of editions.

### What is an ontology?
A formal representation of concepts (classes), instances, and relationships:
- **is-a**: DeepLearning is-a MachineLearning
- **part-of**: NLP is-part-of AI
- **related-to**: FPGA related-to EDA
- **disjoint**: Virtual disjoint-with Physical

### Learning path using this repo

**Step 1 — Concept extraction (Tier 1–2)**
WikiCFP event pages have a `Categories` field with free-text tags
(e.g. "machine learning", "neural networks", "pattern recognition").
Collect all tags → raw concept candidates.

**Step 2 — Synonym grouping (Tier 2, Qdrant)**
Use `nomic-embed-text` embeddings to cluster synonymous tags:
`["ML", "machine learning", "statistical learning"]` → single concept `MachineLearning`

**Step 3 — Hierarchy inference (Tier 3)**
From co-occurrence: conferences tagged both "deep learning" AND "machine learning"
suggest is-a. Qwen3:32b confirms: "Is DeepLearning a subtype of MachineLearning?"

**Step 4 — OWL serialisation**
```python
from owlready2 import *
onto = get_ontology("http://wikicfp.org/conference_domain.owl")
with onto:
    class ResearchField(Thing): pass
    class MachineLearning(ResearchField): pass
    class DeepLearning(MachineLearning): pass   # is-a
```

**Step 5 — Validation + visualisation**
Open `conference_domain.owl` in Protégé (free GUI) to inspect and edit the
hierarchy. Run a reasoner (HermiT) to check consistency.

**Step 6 — Use ontology for smarter classification**
Instead of flat keyword lists, classify new conferences against the OWL hierarchy.
A conference tagged "transformer architecture" → infer DeepLearning → ML → AI
without explicit keyword rules.

### Tools to install
```bash
pip install owlready2 rdflib
# Protégé: https://protege.stanford.edu (Java GUI, free)
```

### Ontology output files (planned)
```
ontology/
  conference_domain.owl     # Full OWL ontology
  concepts.json             # Flat concept list with synonyms and embeddings
  edges.json                # is-a / part-of / related-to triples
  by_conference.json        # Per-conference ontology tag set
```
