# Lesson Plan — Learning Guide for the CFP Pipeline

> A self-paced curriculum covering every concept used in this project.
> Plain-language explanations, but no dumbing down. Each concept is tied to a
> concrete file or schema in this repo so you can flip between this guide and
> the actual code.
>
> Read in order on the first pass; revisit any module on its own later.

---

## Module 0 — The Big Picture

### What is a conference knowledge pipeline?
A program that automatically discovers academic conferences (Calls for Papers,
or CFPs), reads their key facts (when, where, deadlines, topics, organisers),
deduplicates them across sources, classifies them, and produces organised
reports. The goal is a single trusted index of "every conference worth knowing
about, refreshed weekly" without manual upkeep.

### What problem does WikiCFP solve and what are its limitations?
WikiCFP (`www.wikicfp.com`) is a community-edited list of academic CFPs.
Researchers post upcoming conferences there, and other researchers browse by
keyword or category. It solves discovery: you can find a workshop on graph
neural networks without knowing its name in advance. Its limitations:
(a) entries are uneven in completeness — some have only a title and URL;
(b) no taxonomy enforcement — "ML", "machine-learning", and "Machine Learning"
all appear as different tags; (c) no deduplication — the same conference can
appear on WikiCFP, in `ai-deadlines.yml`, and in a CFP email; (d) no quality
signal — predatory venues sit next to NeurIPS. This pipeline addresses all
four.

### Why scraping + LLM classification beats manual curation
Manual curation is high-quality but slow and unscalable: tagging every
new conference into a taxonomy takes hours per week and the queue only
grows. Scraping gets the raw data fast; LLMs (small, local, fast ones at
the bottom of the cascade) handle the structured-extraction task that humans
used to do — but at 200 records per minute instead of 200 per week. Humans
stay in the loop for borderline cases (the review queue, the predatory list),
which is where their judgement actually pays off.

### How the three databases divide the problem
- **PostgreSQL** holds *what we know*. Every fact (the event, its dates, the
  PC chair, the venue, the embedding, the graph edge) lives in one
  PostgreSQL instance. It is the single source of truth.
- **Redis** holds *what we are doing right now*. The fetch queue, per-domain
  rate limit timers, "I'm currently scraping this URL" leases — all live
  in Redis. If Redis crashes, no facts are lost; you just lose your place
  in the queue.
- **DuckDB** holds *nothing at all*. It is a calculator that connects to
  PostgreSQL, runs a fast columnar GROUP BY, and prints a Markdown report.
  No data is stored in DuckDB — it never writes to disk.

This separation matters because it makes failure recovery trivial: lose
PostgreSQL, restore from `pg_dump`. Lose Redis, re-enqueue from the cursor.
Lose DuckDB, type the command again.

### The four-tier cascade — why small models handle most of the work
80% of WikiCFP entries are well-formed and unambiguous: a tiny qwen3:4b model
on a small GPU classifies them correctly in milliseconds. 15% are
slightly ambiguous and get extracted by qwen3:14b. 4% need a tool-calling
qwen3:32b that follows links and reads external sites. The last 1% — genuine
edge cases — get the heavyweight DeepSeek-R1:70b on a DGX-class machine in
overnight batches. The cost-vs-quality knee is at qwen3:14b for this domain;
running everything on the 70b model would be 50× slower and only marginally
more accurate.

### Further reading
*Designing Data-Intensive Applications* by Martin Kleppmann — the foundational
reference for the storage/queue/analytics trichotomy.

---

## Module 1 — PostgreSQL — The Source of Truth

### What PostgreSQL is and why it's still the right choice in 2026
PostgreSQL is a 30-year-old relational database that has steadily added every
feature its competitors invented. It now stores rows, JSON documents, vectors,
and graphs — all queryable in one SQL statement. In 2026 it remains the
default choice for any project that needs durability, transactions, and a
schema you can reason about, because no other open-source database matches
it on breadth without sacrificing depth.

### Relational tables
A table is a grid of rows and columns. Each column has a type (`int`, `text`,
`date`). A **primary key** is the column that uniquely identifies a row
(`event_id` in the `events` table). A **foreign key** is a column that points
at another table's primary key — `events.series_id` points at
`series.series_id`, declaring "this event belongs to that series". This single
mechanism gives PostgreSQL its referential integrity guarantee: you cannot
delete a series while events still reference it.

In this repo, see `context.md §6`: the `events`, `series`, `people`, `venues`,
and `organisations` tables together form a small graph of who-organises-what
that the rest of the pipeline depends on.

### JSONB
Sometimes you need to store data whose shape varies per row — for example, the
raw output of a Tier 1 LLM call, which has different fields depending on the
escalation reason. PostgreSQL's `jsonb` type stores binary-encoded JSON in a
single column, with operators that let you query inside it: `output_json->>'reason' = 'low_confidence'`. The `tier_runs` table uses this for `output_json`.
Treat JSONB as an escape hatch for genuinely schemaless data — not as an
excuse to avoid designing tables.

### Upserts
Scraping is idempotent by design: if you re-scrape the same WikiCFP page, the
pipeline must not create a second copy of the event. PostgreSQL's
`INSERT ... ON CONFLICT (event_id) DO UPDATE SET ...` syntax handles this in
one statement: try to insert; if a row with that primary key already exists,
update it instead. Without upserts, you would need a `SELECT` first, and
between the SELECT and the INSERT another worker could insert the same row —
a classic race condition.

### The COALESCE upsert pattern
`COALESCE(a, b)` returns `a` unless `a` is NULL, in which case it returns `b`.
On upsert, you write `notification = COALESCE(EXCLUDED.notification, events.notification)`
which means "use the new notification date if it's not NULL, otherwise keep
the old one". This stops a low-confidence Tier 1 re-scrape from wiping out a
high-confidence Tier 3 extraction. See `SESSION.md` constraint 8 — never
overwrite `notification`, `camera_ready`, `rank`, or `notes` with NULL.

### Connection strings (DSNs)
A DSN is a URL telling psycopg3 where to find the database. `postgresql://wcfp:wcfp@localhost:5432/wikicfp` reads as: protocol `postgresql`, user `wcfp`,
password `wcfp`, host `localhost`, port `5432`, database `wikicfp`. Stored in
`config.py` as `PG_DSN`. The library hides the network details from
application code.

### psycopg3 vs psycopg2
psycopg3 is the rewritten successor to psycopg2. The differences that matter
for this project: native `async` support (so the pipeline can fetch and write
concurrently), the binary protocol (faster round-trips for vector inserts),
and better Python-typed handling of `jsonb` (no manual `json.dumps`). The
project uses psycopg3 exclusively (`SESSION.md` constraint 2). Never `pip
install psycopg2-binary` — it will silently shadow imports.

### pg_dump and pg_restore
`pg_dump` writes the entire database (schema + data) to a single file.
`pg_restore` reads the file back into a fresh database. The `-F c` flag uses
PostgreSQL's "custom format" — a compressed binary format with selective
restore (you can restore only the `events` table, for example). The pipeline
uses these for GCS portability: every session ends with a `pg_dump` pushed
to GCS; every session begins with a `pg_restore` pulled from GCS. This is
how the same database state moves between your laptop, a borrowed DGX, and
a cloud GPU.

### pgBouncer
PostgreSQL connections are heavyweight (~10 MB of server RAM each). With one
Python process you have 5 connections — fine. With 20 Kubernetes scraper pods
each holding 5, you hit the default `max_connections=100` ceiling. pgBouncer
sits between application and PostgreSQL and pools connections: clients
connect to pgBouncer (cheap), pgBouncer multiplexes them onto a small fixed
pool of real PostgreSQL connections (expensive). Not needed today; flagged
in `arch.md S4` for the K8s migration.

### Further reading
*PostgreSQL: Up and Running* by Regina Obe and Leo Hsu.

---

## Module 2 — pgvector — Semantic Search Inside PostgreSQL

### What a vector is
A vector is a list of numbers — for our purposes, 768 of them. The intuition:
think of each number as a coordinate in a 768-dimensional space. Two pieces
of text that mean similar things land at nearby points; two unrelated
sentences land far apart. The geometry of this space is what makes "find
similar conferences" a single SQL query instead of an ad-hoc text-matching
heuristic.

### What an embedding is
An embedding is the vector produced by feeding text through a model trained
specifically to map similar meanings to similar coordinates. We use
`nomic-embed-text` (768-dim). The same input text always produces the same
vector — embeddings are deterministic given a fixed model version.

### Cosine similarity
Cosine similarity measures the angle between two vectors, ignoring their
magnitudes. The formula: `1 - (a <=> b)` in pgvector syntax, where `<=>`
returns the cosine *distance* (0 = identical, 1 = unrelated, 2 = opposite).
We use a threshold of `0.92` for dedup candidate pairs (any pair above 0.92
is a likely duplicate) and `0.97` for auto-merge (skip the LLM, the pair is
unmistakably the same conference). See `arch.md Q2` for why two thresholds.

### What nomic-embed-text is and why 768-d
A small, locally-runnable embedding model (~300 MB VRAM, can fall back to
CPU). Its output has 768 dimensions — a balance between recall (more
dimensions distinguish more nuances) and storage (50k events × 768 floats
× 4 bytes = ~150 MB; doubling to 1536-d would double that). 768-d is the
established sweet spot for sentence-level embeddings.

### IVFFlat vs HNSW
Both are *approximate* nearest-neighbour indexes — they sacrifice a bit of
recall for query speed. **IVFFlat** clusters vectors into `lists` cells; a
query checks only the nearest cells. Build is fast, memory is small, recall
~95%. **HNSW** builds a multi-layer graph; queries hop through it. Build is
slow, memory is 2-3× the data size, recall ~98%. For 50k–200k events,
IVFFlat is the right call (`arch.md Q8`); HNSW only earns its keep when
interactive query latency exceeds 100 ms p95.

### ANN (Approximate Nearest Neighbour) search
"Approximate" because checking every vector for an exact answer is O(N) — at
50k events that's still fast (~150 ms) but at 1M it's 3 seconds. ANN trades
1-5% recall for 100× speed. For dedup, "close enough" is fine: we only need
to surface candidates the LLM will then confirm. Missing one candidate out
of fifty is not a disaster.

### Why pgvector over a separate vector database
A standalone vector DB (Qdrant, Pinecone) means two stores to back up, two
schemas to evolve, and — critically — no JOINs across modalities. With
pgvector, the cross-modal query in `context.md §5` ("upcoming Europe
ChipDesign conferences ranked by embedding similarity") is one SQL statement.
With Qdrant + PostgreSQL, you would query each, then JOIN in application
code. That's slower, more bug-prone, and doesn't scale.

### Further reading
The pgvector GitHub README is the most concrete resource. For the math: *Mining of Massive Datasets* by Leskovec, Rajaraman, Ullman — chapter 3 on
locality-sensitive hashing.

---

## Module 3 — Apache AGE — Property Graphs Inside PostgreSQL

### What a property graph is
A graph of **nodes** and **edges**, where each node and each edge can carry
**properties** (key-value pairs). A node `Conference` has properties
`{event_id: 12345, acronym: "NeurIPS", start_date: "2025-12-08"}`. An edge
`(Person)-[:CHAIRS]->(Conference)` connects them, optionally with its own
properties like `{role: "general_chair"}`. This is the natural shape of
relationships: many-to-many connections with attributes attached.

### Why a graph is right for this domain
Conferences are dense webs of relationships: a series has editions, each
edition has chairs, each chair has affiliations, sponsors, and co-located
workshops. Asking "which PC chairs at security conferences are affiliated
with Indian institutions?" is a multi-hop question. In SQL it would be
five JOINs, easy to get wrong. In Cypher it is two MATCH clauses, easy to
read. See `context.md §5` for the actual queries.

### Cypher query language
Cypher reads like ASCII art. `(c:Conference)-[:HELD_AT]->(v:Venue)` says
"a node `c` of label `Conference`, connected by an edge of type `HELD_AT`
to a node `v` of label `Venue`". The basic verbs:

- `MATCH` — find a pattern
- `WHERE` — filter
- `RETURN` — emit results
- `CREATE` — make new nodes/edges
- `MERGE` — upsert (create if missing, otherwise match)

### What `*0..` means and why it's dangerous
`-[:IS_A*0..]->` means "follow zero or more `IS_A` edges". It is how you say
"this Concept or anything that is_a this Concept, recursively". The danger:
on a deep graph with cycles or fan-out, the engine can explore an exponential
number of paths. `arch.md Q13` recommends capping at `*0..6` defensively.

### Node labels and edge types in this project
See `context.md §5` for the full list. Key nodes: `Conference`,
`ConferenceSeries`, `Person`, `Organisation`, `Venue`, `City`, `Country`,
`Concept`, `RawTag`. Key edges: `IS_EDITION_OF` (Conference→Series),
`CHAIRS` (Person→Conference), `CLASSIFIED_AS` (Conference→Concept),
`IS_A` (Concept→Concept), `SYNONYM_OF` (RawTag→Concept).

### Why AGE runs inside PostgreSQL
Apache AGE is a PostgreSQL extension. It does not run as a separate process.
A Cypher query is wrapped as `SELECT * FROM cypher('wcfp_graph', $$ ... $$) AS (col agtype);` — a regular SQL statement. This means: one connection,
one transaction, one backup. A graph write and a relational write happen
atomically inside `BEGIN ... COMMIT`. No two-phase commit, no eventual
consistency, no separate cluster.

### The cross-modal query
The single biggest reason for the PostgreSQL+pgvector+AGE choice. One SQL
statement filters by country (relational), traverses the ontology (Cypher),
and ranks by embedding similarity (pgvector). See the worked example at the
end of `context.md §5`. No other open-source stack supports this.

### OWL ontology, Protege, and the .owl export
**OWL** is the W3C standard for representing ontologies — formal vocabularies
of a domain. **Protege** is a desktop app for editing OWL files visually.
In this project the live ontology is the AGE graph; the `.owl` file is a
read-only export produced by `wcfp/ontology.py`. You open the `.owl` in
Protege to inspect or share the taxonomy with collaborators, but you do not
edit it there — edits would not flow back. See `context.md §14`.

### Further reading
*Graph Databases* by Robinson, Webber, and Eifrem (2nd ed.) — chapter 3
covers the property graph model and Cypher.

---

## Module 4 — DuckDB — Analytical Queries Without a Data Warehouse

### OLAP vs OLTP
**OLTP** (Online Transaction Processing) is what PostgreSQL is built for:
many small, fast writes and reads, each touching a few rows. **OLAP**
(Online Analytical Processing) scans millions of rows to compute averages,
counts, and trends. PostgreSQL can do OLAP, but slowly — its row-oriented
storage means a `SELECT AVG(price) FROM events` reads all columns of every
row even if only `price` is needed.

### Columnar storage
DuckDB stores data column-by-column instead of row-by-row. To compute the
average of one column, it reads only that column's bytes. For aggregations
across millions of rows this is 10-100× faster than row storage. The trade-
off: inserting a single new row is slower (it has to update every column
file). That's why PostgreSQL is the writer and DuckDB is the reader.

### postgres_scanner
DuckDB's `postgres_scanner` extension lets DuckDB query PostgreSQL tables
directly: `ATTACH 'postgresql://...' AS pg (TYPE POSTGRES, READ_ONLY)`.
DuckDB then reads PostgreSQL data into its columnar engine on the fly. No
ETL, no copy step, no staleness. See `context.md §7` for the connect helper.

### Why DuckDB never writes in this project
DuckDB owns no data. Reports are recomputed from PostgreSQL on every run.
This is deliberate: it means there's nothing to back up, nothing to keep in
sync, no possibility of report-vs-source drift. If you need an answer that
DuckDB doesn't yet compute, write a new query — never a new table. See
`SESSION.md` constraint 3.

### Window functions
A window function computes a value across a set of rows related to the
current row, without collapsing them into one row like `GROUP BY` does. For
example, `RANK() OVER (PARTITION BY country ORDER BY start_date)` numbers
the conferences per country in date order. `generate_md.py` uses these for
"upcoming-3 per category" reports.

### Parquet
A columnar file format that is the disk equivalent of DuckDB's in-memory
representation. The pipeline exports periodic Parquet snapshots to
`data/archive/` (then synced to GCS). Parquet is the standard interchange
format with downstream tools (Spark, BigQuery, Athena) when this project
ever exposes data outside its own walls.

### Further reading
The DuckDB documentation (`duckdb.org/docs`) is concise and well-written.
For deeper background: *The Data Warehouse Toolkit* by Ralph Kimball.

---

## Module 5 — Redis — The Operational Nerve System

### What Redis is
An in-memory key-value store with rich data structures (strings, lists,
hashes, sorted sets, streams). Sub-millisecond latency. Single-threaded by
design — every command is atomic, no locking required.

### Sorted sets
A sorted set associates each member with a numeric score. The store keeps
members ordered by score. Operations: `ZADD wcfp:queue 12345 url1` (add a
member with score 12345), `ZPOPMIN wcfp:queue` (pop the lowest-scored
member). Both are O(log N). The pipeline uses score = `priority × 1e10 +
epoch_ms`, encoding both priority (high bits) and FIFO order within priority
(low bits). One data structure, two semantics.

### SETNX
"Set if not exists" — `SET key value NX`. Returns success only if the key
was absent. This is how the dedup cache works: before enqueuing a URL, the
pipeline runs `SETNX wcfp:seen:{sha1(url)} 1`. If the SETNX succeeds, this
URL is new — proceed to enqueue. If it fails, another worker already saw
this URL — skip. Atomic and lock-free.

### TTL (Time To Live)
A key can be set to expire after N seconds. Per-domain rate limiting works
like this: after fetching from `ieee.org`, do `SET wcfp:rate:ieee.org 1 EX 8`
(set with 8-second expiry). The next fetch attempt checks for the key's
existence; if present, wait. When the key expires, the rate limit lifts
itself. No background sweeper needed.

### Inflight leases
When a worker dequeues a job, it writes `SET wcfp:inflight:{job_id} <worker_id> EX 600` — an inflight key with 10-minute TTL. If the worker
crashes mid-job, the key expires automatically, and a separate sweeper
re-enqueues the job. Crash safety with no coordinator. See `context.md §8`.

### Dead-letter list
After `MAX_RETRIES` failures, a job is `RPUSH`ed onto `wcfp:dead`. The Tier
4 batch process drains this list. Dead jobs are not lost; they accumulate
until processed. See `arch.md Q5` for the small-machine accumulation problem.

### Why Redis holds zero business data
The project's invariant: wiping Redis loses no facts. The cursor (resume
point), the queue, the rate limiters, the inflight leases — all are
operational. The events, the series, the chairs, the embeddings — all live
in PostgreSQL. This separation is what makes the "ephemeral local state"
model work: GCS only needs to back up PostgreSQL.

### AOF persistence
"Append-Only File": Redis logs every write to disk and `fsync`s every
second. On crash, replay rebuilds in-memory state. A small subset of
operational data (the cursor, the dead-letter list) is business-critical
in practice (`arch.md Q11`); AOF buys you ≤1 second of loss instead of
total amnesia.

### Further reading
*Redis in Action* by Josiah Carlson — slightly dated but the data-structure
chapters are timeless.

---

## Module 6 — LLM Concepts for This Pipeline

### System prompt vs user message
A **system prompt** sets the model's role and rules — invariant across calls.
A **user message** is the per-record payload. In this project, every prompt
in `prompts.md` is a system prompt; the user message is constructed by
`wcfp/llm/tier{N}.py` (a JSON object specific to that record). Splitting them
is what lets the same prompt body apply to thousands of records without
re-tokenising the rules.

### JSON mode
Most modern local models support a `format="json"` flag that constrains
sampling to produce syntactically valid JSON. It catches the easy mistakes
(missing commas, unclosed braces). It does not catch the hard ones (wrong
schema, hallucinated values). See `arch.md Q12` for the recovery cascade
when JSON mode still fails.

### Tool calling
Some models can decide, mid-generation, to invoke a function instead of
emitting more text. The pipeline gives Qwen3:32b tools like `extract_text`,
`find_links`, `classify_category` for use on unknown external sites
(`prompts.md` PROMPT_TIER3). The model emits a structured "I want to call
`find_links` with pattern `20[0-9]{2}`" message; the runtime executes the
function and returns the result; the model continues. DeepSeek-R1 does not
do this — its strength is unbroken reasoning chains.

### Confidence scores
Every prompt asks the model for a `confidence` float in [0, 1]. We treat
0.85 as the escalation threshold: below that, push to the next tier.
Confidence is well-known to be poorly calibrated in LLMs (they're often
overconfident). The two-threshold dedup design (0.92 / 0.97 in
`arch.md Q2`) and the `PROMPT_QUALITY_GUARD` exist precisely to compensate.

### Quantisation (Q4 vs Q8 vs full precision)
Local models ship in compressed forms. **Q4** uses 4 bits per weight (~4×
smaller, runs on tiny GPUs, noticeably worse JSON validity). **Q8** uses 8
bits (~2× smaller, near-original quality). **Full precision (fp16)** is
original. The trade-off is VRAM vs accuracy. `arch.md Q14` recommends
pinning quantisation per `WCFP_MACHINE` profile so we don't silently
degrade on `gpu_small`.

### Context window
The maximum tokens a model can read in one call. Qwen3:14b is ~32k tokens;
mistral-nemo is 128k. A long external conference page can exceed 32k tokens;
the pipeline routes those to mistral-nemo via `LONG_CONTEXT_TOKENS = 32_000`
in `config.py`. Above the window, content is silently truncated by the
runtime — bad for fidelity.

### Escalation cascade
The 4-tier design (`context.md §13`) escalates by raising both model size
*and* model capability: bigger → smarter → tool-using → reasoning. Each
escalation has a named reason from a closed set: `low_confidence`,
`multi_category`, `unknown_site`, `long_context`, `dedup_ambiguous`,
`ontology_edge`. Reasons let us measure where the pipeline is weak.

### DeepSeek-R1 — chain-of-thought reasoning
DeepSeek-R1 is trained to "think out loud" inside `<think>...</think>` tags
before emitting its final answer. The traces are useful for debugging, not
production output (`_strip_thinking()` removes them). DeepSeek-R1 has no
tool-calling support, so it never appears in Tiers 1–3.

### Further reading
The Hugging Face *NLP Course* (free, online) — chapters on tokenisation and
fine-tuning. *Speech and Language Processing* by Jurafsky and Martin (3rd
ed., online draft) — chapters 9–11 on transformers.

---

## Module 7 — The 4-Tier Pipeline

### Tier 1 — qwen3:4b — triage
The cheapest, fastest model. Question: "is this a real CFP at all?"
(`PROMPT_TIER1`). Output: `is_cfp`, `categories[]`, `is_virtual`,
`confidence`. ~200 records per minute on an RTX 3080. About 80% pass with
confidence ≥ 0.85 and proceed to Tier 2.

### Tier 2 — qwen3:14b — full extraction
Reads the WikiCFP detail page text. Question: "extract every field of the
event" (`PROMPT_TIER2`). Output: the full Event object (acronym, name,
dates, deadlines, location, deadlines, sponsors, raw tags, etc.). About 15%
of records reach here.

### Tier 3 — qwen3:32b — tool-calling
For events whose details point to an external site that isn't WikiCFP. The
model uses `extract_text`, `find_links`, etc., to navigate the site and
fill in missing fields. About 4% of records.

### Tier 4 — deepseek-r1:70b — overnight batch
The hardest 1%: contradictions, ontology edges that need careful reasoning,
unresolvable dedup pairs, and the `wcfp:dead` queue. Always final — Tier 4
output is committed without further escalation. Runs only on `dgx`.

### PROMPT_QUALITY_GUARD — the new pre-write gate
A separate prompt that runs *before* the DB write. It checks six failure
modes: predatory publisher, journal-not-conference, invented_url,
wrong_rank, date_anomaly, location_contradiction. Severity outcomes: `block`
(send to dead-letter), `warn` (write with `quality_flag=true`), `ok`
(proceed). It exists because the cascade can produce records the model
believes confidently but a separate sceptical pass catches as wrong. See
`prompts.md` PROMPT_QUALITY_GUARD.

### Escalation reasons
- `low_confidence` — the tier's confidence fell below threshold
- `multi_category` — the model emitted >1 category and the orchestrator wants
  Tier 3 to disambiguate
- `unknown_site` — the source URL is not a known parser, push to Tier 3 for
  tool-calling discovery
- `long_context` — page exceeds the model's context window
- `dedup_ambiguous` — pgvector says "maybe duplicate", needs LLM yes/no
- `ontology_edge` — Tier 4 should propose IS_A / RELATED_TO edges

---

## Module 8 — The Knowledge Graph — Ontology and Taxonomy

### What an ontology is
A formal vocabulary for a domain, with named relationships between terms.
Not just a tag list — an ontology says "MachineLearning IS_A
ComputerScience" and "DiffusionModels IS_A GenerativeAI RELATED_TO
ImageSynthesis". Software can reason over these relationships (find all
events about a parent topic, find topics frequently co-occurring, etc.).

### IS_A vs PART_OF vs RELATED_TO
- **IS_A** — taxonomic. "ComputerVision IS_A MachineLearning" means every
  ComputerVision event is also a MachineLearning event. Strict.
- **PART_OF** — meronymic. "Tokeniser PART_OF Transformer" means tokenisers
  are components of transformers. Used less in this project; mostly for
  workshop-of-conference relationships.
- **RELATED_TO** — soft association with a `weight` property. "DifferentialPrivacy RELATED_TO MachineLearning {weight: 0.7}". Used for cross-cutting
  concepts that don't fit cleanly under one parent.

### SYNONYM_OF
Edges from `RawTag` (the lower-cased string scraped from WikiCFP) to a
canonical `Concept` node. "nlp", "natural language processing", and
"natural-language-processing" all become RawTag nodes pointing
`SYNONYM_OF` at the Concept "NaturalLanguageProcessing". This is how the
pipeline normalises messy human tags into a clean taxonomy.

### Why bottom-up, not top-down
We do not start with a hand-curated tree of every CS sub-field — that would
take months and would be obsolete in a year. Instead the ontology grows
from real data: every scraped RawTag is clustered by embedding similarity
into a Concept; co-occurrence statistics suggest IS_A edges; Tier 4 reviews
each proposal. The taxonomy reflects the corpus, not an editor's bias.

### Why human review is still needed
LLMs propose IS_A edges with confidence scores; humans review the borderline
0.5–0.85 ones. See `arch.md S3` for the review queue design. The model is
not the final authority on the structure of computer science.

### The bootstrap problem
On the very first run, there are no Concept nodes, so clustering has no
seeds and IS_A inference has nothing to attach to. `arch.md Q3`'s answer:
seed the 13 `Category` enum values (AI, ML, Security, …) as root Concepts
under a `ResearchField` super-root, with `RELATED_TO` cross-edges for
cross-cutting concepts. Provided by a one-time `bootstrap-ontology` CLI
command.

### Further reading
*Semantic Web for the Working Ontologist* by Allemang, Hendler, and Gandon —
the practical handbook. Skip the SPARQL-heavy sections; you'll be writing
Cypher.

---

## Module 9 — Scraping, Politeness, and Rate Limiting

### robots.txt
A text file at the root of every well-behaved website (`example.com/robots.txt`) listing which paths crawlers should and should not fetch. Honouring
it is both ethical and legally prudent. The pipeline caches each domain's
robots.txt for 24h in `wcfp:robots:{domain}` and refuses any disallowed URL
(generates a `FatalError`).

### Gaussian delay
Between fetches to the same domain, the pipeline waits a random number of
seconds drawn from a normal distribution centred at 8 seconds, standard
deviation 2.5 (so 95% of waits are between 3 and 13 seconds). Why not a
fixed 8 seconds? Fixed delays are detectable: a server seeing requests
exactly 8 seconds apart knows you're a bot. Random delays look more like a
human reading. `SESSION.md` constraint 9.

### Per-domain rate limiting
Each domain gets its own TTL key in Redis. WikiCFP is one domain, so all
WikiCFP fetches share one rate limiter. External conference websites each
get their own. See Module 5.

### The 10% chance of a 15–45s pause
Once per ~10 requests, the pipeline takes a longer pause to simulate a
human getting up for coffee. Helps look more organic and gives the target
server breathing room.

### Crawl throughput estimation
At Gaussian(8, 2.5), throughput is ~7.5 requests/minute = 450/hour. The
WikiCFP first run is ~5,680 pages, so ~12.6 hours of pure delay. After
the first run, the seen-URL cache (30-day TTL) means most pages are
skipped — steady state is ~30 minutes per session. See `arch.md Q7`.

### Seen-URL dedup
Before enqueuing, the scraper does `SETNX wcfp:seen:{sha1(url)} 1` with
30-day TTL. If the SETNX fails, the URL was queued recently — skip. Without
this, every nightly cron would re-queue the same 5,680 WikiCFP URLs and
the queue would explode.

### Further reading
*Web Scraping with Python* by Ryan Mitchell — chapter on crawl etiquette.

---

## Module 10 — Data Quality and Deduplication

### The dedup problem
One conference (e.g. NeurIPS 2025) appears on WikiCFP, in `ai-deadlines.yml`,
on its own website, and in a CFP email. Each appearance gets a different
`event_id` (since the source URLs differ). Without dedup, the report shows
NeurIPS four times, with four different sets of fields. With dedup, one
canonical row.

### pgvector ANN candidate generation
Step 1: embed every event's description into a 768-d vector. Step 2: for
each event, query pgvector's IVFFlat index for the top-5 nearest
neighbours. Step 3: any pair above cosine 0.92 becomes a candidate. This
turns an O(N²) all-pairs problem into O(N · K) with K=5.

### PROMPT_DEDUP — what the model decides
Given two candidate Events, the model returns `{same: bool, same_series:
bool, reason: str}`. `same=true` means same conference instance (same
year). `same_series=true` means same recurring series (any year) — used for
building `PRECEDED_BY` edges in the graph. The acronym normalisation rules
(strip year, strip ordinals, lowercase, etc.) are spelled out in
`prompts.md`.

### PROMPT_QUALITY_GUARD — six failure modes
See Module 7. Explicitly: predatory publisher, journal-not-conference,
invented URL, wrong rank, date anomaly, location contradiction. Each
becomes a flag; flags can be `block`, `warn`, or `ok`. Records flagged
`block` go to dead-letter; `warn` is written with `quality_flag=true` and
appears in the human review CLI.

### COALESCE upsert (recap)
On dedup merge: keep the row with higher confidence as the winner; copy
non-null fields from the loser into nulls of the winner via COALESCE; mark
the loser `superseded_by` (audit trail, do not delete). See `arch.md Q6`.

### Predatory conferences and Beall's list
Predatory conferences accept papers with no peer review for a fee, exist
mainly to extract author payments, and pollute search results. Jeffrey
Beall (a librarian) maintained a public list of predatory publishers from
2008–2017; the list survives in archived form. Its successors (Cabells,
the snapshots maintained by community sites) are imperfect but valuable.
The pipeline ships a `data/blocklist.txt` curated from public sources
(`arch.md S10`) and checks it before enqueue.

### CORE rankings — A*, A, B, C
The CORE Conference Ranking Portal (`portal.core.edu.au`) ranks
~1,000 international computer science conferences by quality:
- **A\*** — top of field, definitive venue (e.g. NeurIPS, SIGCOMM)
- **A** — excellent (e.g. WWW, EuroSys)
- **B** — good (e.g. NetSys, IUCC)
- **C** — known but unranked-prestige (e.g. regional or specialty venues)

LLMs are bad at guessing rank from prestige; the pipeline imports the CORE
table directly (`arch.md S12`) and overrides any LLM-emitted rank that
disagrees.

### Further reading
*Garbage In, Garbage Out* (1957 saying — no specific book). For dedup
theory: *Data Matching* by Peter Christen.

---

## Module 11 — GCS + rclone + Machine Lifecycle

### What Google Cloud Storage is
GCS is Google's object store — like S3 on AWS. You store **objects** (blobs
of bytes) inside **buckets** (named containers), addressed by **prefixes**
(directory-like keys). `gs://wcfp-data/prod/pg_backup/latest.dump` reads
as: bucket `wcfp-data`, prefix `prod/pg_backup/`, object `latest.dump`.
Cheap (~$0.02/GB/month), durable (11 nines), region-replicated.

### rclone
A CLI tool that talks to many cloud storage providers (GCS, S3, B2,
Backblaze, Dropbox) with one syntax. `rclone copy ./data/pg_backup/
gcs:wcfp-data/prod/pg_backup/` uploads a directory. Like rsync, but for
the cloud. The pipeline uses it because it's provider-agnostic — switching
from GCS to B2 needs only a config change.

### The pull→run→sync→wipe lifecycle
Every session: (1) **pull** the latest pg_dump from GCS, restore into a
fresh local PostgreSQL; (2) **run** the pipeline, which writes new data;
(3) **sync** by dumping PostgreSQL back to GCS and pushing reports to git;
(4) **wipe** the local Docker volumes. After step 4, the laptop has zero
state. The next session, on possibly a different machine, starts from
step 1. See `context.md §18`.

This is a feature, not a workaround: ephemeral local state means GCS is
the *only* source of truth between sessions, which means there is no "two
laptops disagree" failure mode.

### pg_dump format flags (`-F c`)
`-F c` selects the "custom" format: a compressed binary dump with a table
of contents. Advantages over the default plain-text SQL dump:
~5× smaller, much faster restore, supports selective restore (`pg_restore -t events` restores only the events table). `pg_dump -F c -f latest.dump`
is the project's canonical backup command.

### Workload Identity (GKE)
On Kubernetes, every pod has a Kubernetes service account. Workload
Identity binds a Kubernetes SA to a Google Cloud IAM service account; the
pod's gcloud/rclone calls are then authenticated automatically via the
metadata server. **No JSON key file in any image.** No env var leaks. See
`arch.md §5.6`.

### Why heavy data never goes in git
git is bad at large binaries (history bloats forever) and worse at frequently-
changing ones (every snapshot is a new full file in the pack). pg_dump
files (~10 GB) and embedding tables go to GCS. Reports and seed data go to
git because they are small, human-readable, and deserve diff history.

### Further reading
The GCS docs `cloud.google.com/storage/docs`. For object stores
generally: the AWS S3 chapter of *Cloud Native Patterns* by Cornelia Davis.

---

## Module 12 — Kubernetes (For the Future Migration)

### Kubernetes — the one-sentence honest version
Kubernetes is a system that takes container images and runs them on a
cluster of machines, restarting them when they crash, scheduling them on
nodes with the right resources, and giving them stable names. It is more
complex than Docker Compose; you only need it when you outgrow one
machine.

### Pods, Deployments, StatefulSets, Jobs, CronJobs
- **Pod** — one or more containers that share a network and lifecycle.
  The smallest schedulable unit. You rarely create pods directly.
- **Deployment** — runs N stateless replica pods; updates rollingly. Use
  for stateless services (the scraper).
- **StatefulSet** — like Deployment but with stable identities and per-pod
  storage. Use for stateful services (PostgreSQL, Redis).
- **Job** — runs a pod to completion, then stops. Use for one-shot work
  (a pipeline run).
- **CronJob** — Job + cron schedule. Use for scheduled work (weekly
  pipeline run).

### Node pools
A node pool is a group of nodes with the same machine type. The project
plan (`arch.md §5.1`) has four pools: `cpu` (always-on small), `gpu-small`
(T4 spot), `gpu-large` (L4/A10 spot), `gpu-xlarge` (A100 for Tier 4
batches). Each pool autoscales 0–N independently.

### Cluster Autoscaler
Watches for unschedulable pods (e.g. a Job that wants a GPU but no GPU node
exists) and adds nodes to the matching pool. When a pod finishes and the
node sits idle for 10 minutes, it is removed. This is what makes the
"cost only when running" promise real.

### Spot / Preemptible nodes
GCP/AWS sell their idle GPU capacity at 60-91% off, on the condition that
they can reclaim it with 30-90 seconds notice. Perfect for batch work that
can resume from a checkpoint. The pipeline's inflight-lease and idempotent-
upsert design (`arch.md R5`) is exactly what makes spot nodes safe.

### PersistentVolumeClaim (PVC)
A request for storage that survives pod restarts. PostgreSQL needs a 50 GB
SSD PVC; the StatefulSet binds the PVC to its pod, so even if the pod is
rescheduled to a different node, the data follows. PVCs are zonal on GKE —
moving across zones costs a snapshot+restore.

### KEDA — Kubernetes Event-Driven Autoscaler
A Kubernetes add-on that scales pods based on external metrics. The
pipeline's plan: scale scraper pods 0→20 based on `LLEN wcfp:queue` (Redis
queue depth). `arch.md S7`. Useful when Tier 3 follows external links to
hundreds of conference websites — the per-domain rate limit doesn't
saturate any single one.

### Cloud SQL — when it beats a StatefulSet
Cloud SQL is Google's managed PostgreSQL: backups, failover, patches all
handled. It is what every K8s tutorial recommends for the database. **For
this project it does not work** because Apache AGE is not in the Cloud SQL
extension allowlist (`arch.md Q1`). The fallback is StatefulSet+PVC.

### GKE Autopilot — what "fully managed" means
With GKE Autopilot, you submit pods and Google decides which nodes to run
them on; you don't manage node pools. Pricing is per-pod-hour. For
infrequent workloads (~weekly cron run), Autopilot is cheaper than Standard
because the per-cluster fee disappears (`arch.md §5.8`).

### Why Docker Compose stays for local dev
Even after a K8s adoption, you still want a one-command `docker compose
up` on a laptop for development and CI. The plan: maintain both. Same
images, two orchestration files. See `arch.md Q9`.

### Further reading
*Kubernetes in Action* by Marko Lukša (2nd ed.) — the most readable
intro. *Cloud Native DevOps with Kubernetes* by John Arundel and Justin
Domingus.

---

## Module 13 — Observability and Operations

### What observability means
The textbook trinity:
- **Logs** — text events, one per occurrence ("scraped url X at time Y").
- **Metrics** — numbers over time ("scrape RPS = 7.5").
- **Traces** — the path of a single request through the system ("URL X →
  fetch → parse → tier1 → tier2 → DB").

A working pipeline emits all three. We start with logs and metrics; traces
are deferred (they need an OpenTelemetry collector).

### Prometheus
A metrics server that scrapes metric endpoints (HTTP `/metrics`) every
~15 seconds, stores time series, and lets you query them in PromQL.
Industry standard for the Linux side of the world.

### What a metric looks like
```
wcfp_scrape_pages_total{source="wikicfp",outcome="ok"} 4123
wcfp_scrape_pages_total{source="wikicfp",outcome="failed"} 12
wcfp_tier_records_total{tier="1",outcome="escalated"} 219
```
Each line is a counter or gauge with labels. You query
`rate(wcfp_scrape_pages_total[5m])` to get pages-per-second over the last
5 minutes.

### Grafana
A dashboard tool that reads from Prometheus (and many other sources). You
draw graphs, alerts, and tables. The pipeline's planned dashboard
sections (`arch.md S8`): throughput, quality, cost, errors.

### Which metrics matter for this pipeline
In priority order:
1. `wcfp_queue_depth` — is the pipeline alive?
2. `wcfp_tier_records_total{outcome="escalated"}` — how often are we
   pushing work to expensive tiers?
3. `wcfp_dedup_decisions_total` — how often do we find duplicates? (low
   numbers may mean dedup is missing them)
4. `wcfp_embedding_seconds` — embedding latency = bottleneck signal
5. `wcfp_llm_parse_failures_total` — JSON-mode failures by model

See the full list in `arch.md S8`.

### Health check endpoint
`/healthz` returns `200 OK` with a small JSON body when the pipeline is
healthy. Kubernetes uses it to decide when to restart a pod. The plan
(`arch.md S2`): a small FastAPI app exposing `/health`, `/metrics`,
`/queue`, `/runs`. Slot it into `wcfp/cli.py serve --port 8080`.

### Structured logging
Free-text logs ("scraped url okay") are unsearchable at scale. Structured
JSON logs (`{"event": "scrape_ok", "url": "...", "duration_ms": 312}`)
parse cleanly into log aggregation tools (Loki, Cloud Logging, ElasticSearch). Use Python's `logging` with a JSON formatter; emit events,
not sentences.

### The audit trail — scrape_session_id
Every write carries a `scrape_session_id` foreign key into a `pg.scrape_sessions` table that records `(session_id, started_at, finished_at,
machine, git_sha, prompts_md_sha)`. If a bad session pollutes the corpus,
`DELETE FROM events WHERE last_session_id = X` rolls it back cleanly. Also
pins which `prompts.md` version produced which records — essential when
debugging a prompt regression. See `arch.md S9`.

### Further reading
*Site Reliability Engineering* (the Google book, free online) — chapters
on monitoring distributed systems. *Observability Engineering* by Charity
Majors et al. — the SRE-flavoured update.

---

## Glossary

| Term | One-line definition |
|------|---------------------|
| AGE | Apache AGE — a PostgreSQL extension that adds property-graph storage and Cypher query support inside PG. |
| ANN | Approximate Nearest Neighbour — index-based vector search that trades 1-5% recall for 100× speed. |
| AOF | Append-Only File — Redis persistence mode that logs every write to disk; replay rebuilds in-memory state on crash. |
| CFP | Call for Papers — an academic announcement soliciting paper submissions for a conference or journal. |
| CMT | Microsoft's Conference Management Toolkit — a paper submission system used by many ACM and IEEE venues. |
| COALESCE | SQL function returning the first non-null argument; used in upserts to avoid wiping known fields with new NULLs. |
| CORE ranking | Australian-led conference quality ranking with tiers A*/A/B/C; authoritative for CS conferences. |
| Cypher | Neo4j-originated graph query language used by Apache AGE; reads as ASCII art for nodes and edges. |
| Dead-letter | A list of jobs that failed too many times; processed by Tier 4 batch rather than discarded. |
| DGX | Nvidia's high-end GPU server (A100/H100, 80+ GB VRAM); needed for Tier 4 deepseek-r1:70b. |
| DSN | Data Source Name — the URL string identifying a database (`postgresql://user:pass@host:port/db`). |
| DuckDB | An in-process columnar OLAP database used here as a read-only analytics layer over PostgreSQL. |
| EDAS | Editor's Assistant — a paper submission system used heavily in IEEE communications and networking conferences. |
| EasyChair | A widely used paper submission and conference management system, especially in academic CS. |
| Embedding | A dense numeric vector (here 768-d) representing the semantic content of a piece of text. |
| Escalation | The act of pushing a record to the next-higher LLM tier when the current tier's confidence is too low. |
| GCS | Google Cloud Storage — Google's object store, used here as the off-machine persistence layer. |
| HNSW | Hierarchical Navigable Small World — a graph-based ANN index; higher recall than IVFFlat but bigger and slower to build. |
| IVFFlat | Inverted-File Flat — pgvector's default ANN index; clusters vectors into `lists` cells for fast lookup. |
| Inflight lease | A short-lived Redis key declaring "worker X has dequeued job Y"; auto-expires to prevent lost work on crash. |
| JSON mode | LLM runtime flag that constrains sampling to produce syntactically valid JSON. |
| KEDA | Kubernetes Event-Driven Autoscaler — scales pods based on external metrics like queue depth. |
| nomic-embed-text | Local-runnable 768-d embedding model from Nomic; runs on CPU or ~300 MB VRAM via Ollama. |
| OWL | Web Ontology Language — W3C standard for representing ontologies; produced as a read-only export from AGE here. |
| pgBouncer | Connection pooler in front of PostgreSQL; needed when many app pods exhaust PG's connection limit. |
| pgvector | PostgreSQL extension adding a `vector` column type and ANN indexes (IVFFlat, HNSW). |
| Predatory conference | A conference that accepts papers with no peer review for a fee; pollutes search results if not filtered. |
| psycopg3 | The current-generation Python driver for PostgreSQL; supports async, binary protocol, and native jsonb. |
| Quantisation | Compressing model weights to lower-bit representations (Q4, Q8) to fit smaller GPUs at some accuracy cost. |
| rclone | A provider-agnostic CLI for cloud storage (GCS, S3, B2, etc.); used to push pg_dump and pull state. |
| Redis | An in-memory key-value store with rich data structures, used here for the queue, rate limits, and inflight leases. |
| SETNX | "Set if Not eXists" — atomic Redis primitive used for dedup-before-enqueue and per-domain rate limit. |
| Spot node | A discounted cloud VM that the provider can reclaim with brief notice; used for GPU batch work. |
| StatefulSet | Kubernetes resource for stateful services with stable identities and per-pod storage; used for PostgreSQL/Redis. |
| Tier | One of the four LLM cascade levels (Tier 1=qwen3:4b, Tier 2=qwen3:14b, Tier 3=qwen3:32b, Tier 4=deepseek-r1:70b). |
| Tool calling | LLM feature where the model emits a structured "call function X with args Y" message; used in Tier 3. |
| TTL | Time To Live — a Redis key's expiry duration; underpins rate limiting, seen-URL dedup, and inflight leases. |
| Upsert | INSERT ... ON CONFLICT DO UPDATE — atomic insert-or-update that keeps scraping idempotent. |
| WAL | Write-Ahead Log — PostgreSQL's durability mechanism; underlies replication and point-in-time recovery. |
| WikiCFP | The community-edited CFP listing site at `www.wikicfp.com`; the pipeline's primary scrape source. |
| Workload Identity | GKE feature binding a Kubernetes service account to a GCP IAM identity; removes the need for JSON key files. |
