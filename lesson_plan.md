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
A DSN is a URL telling psycopg3 where to find the database. `postgresql://cfp:cfp@localhost:5432/cfp` reads as: protocol `postgresql`, user `cfp`,
password `cfp`, host `localhost`, port `5432`, database `wikicfp`. Stored in
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
A Cypher query is wrapped as `SELECT * FROM cypher('cfp_graph', $$ ... $$) AS (col agtype);` — a regular SQL statement. This means: one connection,
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
read-only export produced by `cfp/ontology.py`. You open the `.owl` in
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
members ordered by score. Operations: `ZADD cfp:queue 12345 url1` (add a
member with score 12345), `ZPOPMIN cfp:queue` (pop the lowest-scored
member). Both are O(log N). The pipeline uses score = `priority × 1e10 +
epoch_ms`, encoding both priority (high bits) and FIFO order within priority
(low bits). One data structure, two semantics.

### SETNX
"Set if not exists" — `SET key value NX`. Returns success only if the key
was absent. This is how the dedup cache works: before enqueuing a URL, the
pipeline runs `SETNX cfp:seen:{sha1(url)} 1`. If the SETNX succeeds, this
URL is new — proceed to enqueue. If it fails, another worker already saw
this URL — skip. Atomic and lock-free.

### TTL (Time To Live)
A key can be set to expire after N seconds. Per-domain rate limiting works
like this: after fetching from `ieee.org`, do `SET cfp:rate:ieee.org 1 EX 8`
(set with 8-second expiry). The next fetch attempt checks for the key's
existence; if present, wait. When the key expires, the rate limit lifts
itself. No background sweeper needed.

### Inflight leases
When a worker dequeues a job, it writes `SET cfp:inflight:{job_id} <worker_id> EX 600` — an inflight key with 10-minute TTL. If the worker
crashes mid-job, the key expires automatically, and a separate sweeper
re-enqueues the job. Crash safety with no coordinator. See `context.md §8`.

### Dead-letter list
After `MAX_RETRIES` failures, a job is `RPUSH`ed onto `cfp:dead`. The Tier
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
`cfp/llm/tier{N}.py` (a JSON object specific to that record). Splitting them
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
pinning quantisation per `CFP_MACHINE` profile so we don't silently
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
unresolvable dedup pairs, and the `cfp:dead` queue. Always final — Tier 4
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
robots.txt for 24h in `cfp:robots:{domain}` and refuses any disallowed URL
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
Before enqueuing, the scraper does `SETNX cfp:seen:{sha1(url)} 1` with
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
(directory-like keys). `gs://cfp-data/prod/pg_backup/latest.dump` reads
as: bucket `cfp-data`, prefix `prod/pg_backup/`, object `latest.dump`.
Cheap (~$0.02/GB/month), durable (11 nines), region-replicated.

### rclone
A CLI tool that talks to many cloud storage providers (GCS, S3, B2,
Backblaze, Dropbox) with one syntax. `rclone copy ./data/pg_backup/
gcs:cfp-data/prod/pg_backup/` uploads a directory. Like rsync, but for
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
pipeline's plan: scale scraper pods 0→20 based on `LLEN cfp:queue` (Redis
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
cfp_scrape_pages_total{source="wikicfp",outcome="ok"} 4123
cfp_scrape_pages_total{source="wikicfp",outcome="failed"} 12
cfp_tier_records_total{tier="1",outcome="escalated"} 219
```
Each line is a counter or gauge with labels. You query
`rate(cfp_scrape_pages_total[5m])` to get pages-per-second over the last
5 minutes.

### Grafana
A dashboard tool that reads from Prometheus (and many other sources). You
draw graphs, alerts, and tables. The pipeline's planned dashboard
sections (`arch.md S8`): throughput, quality, cost, errors.

### Which metrics matter for this pipeline
In priority order:
1. `cfp_queue_depth` — is the pipeline alive?
2. `cfp_tier_records_total{outcome="escalated"}` — how often are we
   pushing work to expensive tiers?
3. `cfp_dedup_decisions_total` — how often do we find duplicates? (low
   numbers may mean dedup is missing them)
4. `cfp_embedding_seconds` — embedding latency = bottleneck signal
5. `cfp_llm_parse_failures_total` — JSON-mode failures by model

See the full list in `arch.md S8`.

### Health check endpoint
`/healthz` returns `200 OK` with a small JSON body when the pipeline is
healthy. Kubernetes uses it to decide when to restart a pod. The plan
(`arch.md S2`): a small FastAPI app exposing `/health`, `/metrics`,
`/queue`, `/runs`. Slot it into `cfp/cli.py serve --port 8080`.

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

## Module 14 — Async Python and asyncio

### What sync vs async means
A **synchronous** function runs from start to finish, blocking the calling
thread until it returns. An **asynchronous** function can pause itself
(`await`) while waiting for slow I/O, letting the same thread make progress
on other work in the meantime. Both run on a single OS thread; the difference
is whether work *yields* during waits or hogs the CPU.

### Why we use it
Scraping WikiCFP is overwhelmingly I/O-bound: 99% of wall-clock time is spent
waiting for HTTP responses. A synchronous loop with `requests.get()` leaves
the CPU idle for entire seconds per page. Switching to `aiohttp` lets one
worker overlap multiple in-flight requests against different domains, giving
~3–5× throughput at zero extra CPU cost. This is the single highest-leverage
performance change available without parallel hardware.

### How it fits
`cfp/fetch.py` will use `aiohttp.ClientSession` and `asyncio.gather()` to
fan out concurrent fetches against multiple domains, replacing the synchronous
`requests` library used in the original `scraper.py` — see `arch.md §S13`
("Async HTTP in fetch.py"). Per-domain Gaussian delay still serialises calls
to the *same* host, so concurrency only helps across domains.

### Further reading
*Using Asyncio in Python* by Caleb Hattingh.

---

## Module 15 — BeautifulSoup4 and HTML Parsing

### What it is
BeautifulSoup4 (BS4) is a Python library that turns raw HTML bytes into a
navigable tree of Python objects. The **DOM** (Document Object Model) is the
formal name for that tree: every tag becomes a node, every text fragment
becomes a leaf (a `NavigableString`). BS4 exposes traversal methods like
`find()` (first match), `find_all()` (every match), and CSS-style
`select(".classname")` queries on top of the tree.

### Why we use it
WikiCFP serves plain server-rendered HTML, not JSON. To get structured event
records we have to extract them from the page's table layout — and that
layout has a nasty quirk: each conference occupies *two* `<tr>` rows (a title
row followed by a detail row). BS4 handles this paired-row pattern cleanly
because it returns siblings in source order, so `zip(rows[::2], rows[1::2])`
walks event records correctly. Using regex on raw HTML would be far more
fragile.

### How it fits
`cfp/parsers/wikicfp.py` copies `find_data_table()` and `parse_table()`
verbatim from the original `scraper.py` — see `codegen/04_parsers.md`
lines 90–143. Both functions take a `BeautifulSoup` object built with the
`lxml` parser backend (faster than the stdlib `html.parser`).

### Further reading
*Web Scraping with Python* by Ryan Mitchell — the BS4 chapter.

---

## Module 16 — HTTP Semantics, Headers, and Retry Logic

### What it is
HTTP is a request/response protocol. Each response carries a numeric **status
code**: `200 OK` (success), `301`/`302` (redirect — follow the `Location`
header), `404 Not Found` (the URL never existed or is gone), `410 Gone` (it
was here and is permanently removed), `429 Too Many Requests` (slow down),
`5xx` (server problem). **Headers** are key-value pairs attached to every
request and response: `User-Agent` identifies the client, `Accept` declares
acceptable response formats, `Cookie` carries session state, `Referer`
declares where the request was linked from.

### Why we use it
Retries cannot be naive. A `404` is **fatal** — retrying makes no difference;
the page is gone. A `429` or `503` is **retryable** — the server is asking
you to wait. Conflating them either gives up too early on transient outages
or hammers servers with hopeless requests. Adding **jitter** (random noise)
to backoff prevents the *thundering herd*: when many workers fail at the
same instant, jitter spreads their retries across time so the recovering
server doesn't get re-stampeded.

### How it fits
`cfp/fetch.py` raises `RetryableError` on 429/5xx/timeout and `FatalError`
on 404/410/robots-disallow — see `context.md §15`. The retry policy reads
`MAX_RETRIES=5`, `RETRY_BACKOFF_BASE=2.0`, and `RETRY_BACKOFF_CAP=600` from
`cfp/config.py` (codegen/01 lines 79–81). After the cap, jobs go to
`cfp:dead` for Tier 4 batch processing.

### Further reading
*HTTP: The Definitive Guide* by Gourley and Totty.

---

## Module 17 — Date and Time Handling in Python

### What it is
`datetime.date` is a calendar date with year/month/day only — no time of day,
no timezone. `datetime.datetime` adds hours/minutes/seconds and (when made
*aware*) a timezone. **ISO-8601** is the international format `YYYY-MM-DD`
that sorts lexicographically and parses unambiguously. The `dateutil` library
adds `dateutil.parser.parse(s, fuzzy=True)`, which gracefully handles messy
human input like "Mar 15, 2026" or "March 15th 2026".

### Why we use it
Every conference field this pipeline cares about — submission deadline,
notification, camera-ready, start/end — is a calendar date, not a moment in
time. Storing them as `date` (not `datetime`) avoids meaningless timezone
conversions and 23:59 vs 00:00 ambiguity. WikiCFP and CFP emails write dates
in dozens of formats ("Mar 15", "March 15, 2026", "15.3.26", "TBA"), so we
need fuzzy parsing — and a guard that returns `None` (not a wrong guess) for
"TBD" or "N/A".

### How it fits
`_safe_parse_date()` in `cfp/parsers/wikicfp.py` (codegen/04 lines 48–66) is
the pipeline's single entry point for date parsing: it calls
`dateutil.parser.parse(fuzzy=True)`, returns `None` on failure, and downstream
code uses `COALESCE` to avoid overwriting a known date with a parse failure.
Partial dates ("March 2026") are pinned to the first of the month with
reduced confidence in the LLM output.

### Further reading
The `dateutil` documentation; *Python Cookbook* by Beazley and Jones — the
"Dates and Times" chapter.

---

## Module 18 — Docker and Docker Compose

### What it is
A **container** is an isolated process with its own filesystem and network
namespace, sharing the host kernel. An **image** is a frozen blueprint;
running it produces a container. **Docker Compose** is a higher-level tool
that reads a `docker-compose.yml` file and starts/stops a set of related
containers as one logical unit. **Named volumes** are persistent disk areas
that survive `docker compose restart` (and even `docker compose down`) but
are wiped by `docker compose down -v`.

### Why we use it
Installing PostgreSQL with the right pgvector extension version directly on a
laptop is a per-OS chore that breaks on every distro upgrade. A Docker image
guarantees that PostgreSQL 16 + pgvector behaves identically on your laptop,
a borrowed DGX, and a cloud GPU. The two-service Compose file (PostgreSQL +
Redis) is the smallest reproducible foundation the pipeline can rest on. v1
intentionally uses `pgvector/pgvector:pg16` rather than `apache/age:PG16_latest`
to keep the AGE complexity out of the way until v2 needs it.

### How it fits
`docker-compose.yml` (codegen/13) declares two services, `postgres` and
`redis`, with named volumes `pg_data` and `redis_data`. `make wipe` runs
`docker compose down -v` to fully reset local state — central to the
ephemeral-local-state lifecycle in `context.md §18`.

### Further reading
*Docker: Up & Running* by Sean Kane and Karl Matthias.

---

## Module 19 — Git Workflow for Solo Projects

### What it is
A **commit** is a snapshot of the working tree plus a message; not a "save"
button but a checkpoint that you can later return to or compare against.
**Branches** are independent lines of commits. **Staging** (`git add`) lets
you choose which changes go into the next commit. `git log --oneline` shows
the project's history as a one-line-per-commit list. `git diff --cached`
shows what is currently staged but not yet committed.

### Why we use it
For a solo project working straight on `main` is fine — branches matter most
for code review with collaborators. The discipline that *does* matter solo:
imperative-mood commit messages ("Add config.py" not "Added"), atomic
commits (one logical change each), and `git add <files>` not `git add -A`.
The `-A` form sweeps in everything including stray `.env` files and accidental
`pg_dump` artefacts — exactly the secret-leak vector this project must avoid.

### How it fits
`CLAUDE.md` carries the standing instruction "never use `git add -A`" as a
preventative against committing the GCS service-account JSON or a PG dump.
Amend is forbidden after push; create a new commit instead — every push is
the public record.

### Further reading
*Pro Git* by Scott Chacon and Ben Straub (free online).

---

## Module 20 — Scraping Ethics and Legal Considerations

### What it is
`robots.txt` is a plain-text file at the root of a domain telling crawlers
which paths are off-limits (`Disallow: /private/`) and how slowly to fetch
(`Crawl-delay: 8`). **ToS** (Terms of Service) is the site's legal contract;
many forbid automated access. The **CFAA** (Computer Fraud and Abuse Act) in
US law makes accessing a system "without authorisation" a federal crime —
courts have interpreted this narrowly since *hiQ v. LinkedIn*, but the line
still exists.

### Why we use it
The pipeline's social licence to operate depends on being indistinguishable
from a careful human reader. That means: honour `robots.txt`, never bypass
authentication, don't scrape behind login walls, randomise inter-request
delays, and pause occasionally to look like a person glancing away. The
`_is_english()` filter is *not* discrimination — it's scope: this project
targets English-language CS venues and saves bandwidth by skipping pages it
won't classify well anyway.

### How it fits
`cfp/config.py` sets `HUMAN_DELAY_MEAN=8.0`, `HUMAN_DELAY_STD=2.5`, and
`HUMAN_DELAY_LONG_PROB=0.10` (codegen/01 lines 72–76) — the inter-request
politeness budget. Robots.txt is fetched per-domain, cached for 24h in
`cfp:robots:{domain}`, and a disallow raises `FatalError`.

### Further reading
*Web Scraping with Python* by Ryan Mitchell — the legal chapter; the EFF
write-ups on *hiQ v. LinkedIn* and *Van Buren v. United States*.

---

## Module 21 — Testing Strategy for This Pipeline

### What it is
**Unit tests** exercise pure functions — given inputs A, assert output B. No
network, no DB. **Fixture-based tests** save real I/O responses to disk
(`tests/fixtures/wikicfp_iccv2026.html`) and run code against them, so a
parser regression is caught without re-hitting WikiCFP. **Contract tests**
for LLMs assert *shape*, not value: given any non-empty input, the output
JSON must contain the keys `is_cfp` (bool) and `confidence` (float), but not
which value those keys take — LLM outputs are stochastic and value-asserts
will flap.

### Why we use it
v1 cannot ship without tests because the pipeline writes to a database that
survives sessions: a silent parser bug pollutes the corpus permanently. The
three-tier strategy (unit / fixture / contract) covers the three failure
classes that have actually occurred during development: regex edge cases,
WikiCFP HTML changes, and LLM JSON-mode escapes.

### How it fits
`tests/fixtures/wikicfp_iccv2026.html` will be the seed fixture for
`tests/test_wikicfp_parser.py`, asserting that `parse_event_detail()` returns
`acronym == "ICCV"` and `start_date == date(2026, 10, 11)`. PostgreSQL is
*not* mocked — per `arch.md` reviewer feedback, tests run against a real
disposable PG database brought up via Docker Compose.

### Further reading
*Test-Driven Development with Python* by Harry Percival; Brian Okken's
*Python Testing with pytest*.

---

## Module 22 — Python Packaging and Virtual Environments

### What it is
A **virtual environment** is an isolated Python installation with its own
`site-packages` directory, created by `python -m venv .venv` and activated
by `source .venv/bin/activate`. **`pip install -r requirements.txt`**
installs every line of the requirements file into the active venv.
**Pinning** (`psycopg[binary]==3.1.18`) freezes the exact version so
`pip install` produces the same result on every machine.

### Why we use it
Without a venv, two projects on the same laptop fight over psycopg2 vs
psycopg3, BeautifulSoup vs bs4 — and a global `pip install` can break
system tools that depend on a specific version. With a venv, `.venv/` is
disposable: `rm -rf .venv && python -m venv .venv && pip install -r
requirements.txt` reproduces the environment from scratch. This project
specifically requires **psycopg3** (not psycopg2): native `async`, binary
protocol, and clean `jsonb` typing all matter for the pipeline's
async-in-aiohttp + vector-write workload.

### How it fits
`setup.sh` creates `.venv/` and runs `pip install -r requirements.txt`. The
requirements file pins `psycopg[binary]` (psycopg3 with the C extension
bundled, no system `libpq-dev` needed) — see `SESSION.md` constraint 2.

### Further reading
The Python Packaging User Guide (`packaging.python.org`); *Effective Python*
by Brett Slatkin — Item 83 on virtual environments.

---

## Module 23 — Environment Variables and 12-Factor Config

### What it is
**Environment variables** are key-value pairs attached to a running process,
inherited from its parent. Python reads them via `os.getenv("KEY", "default")`.
The **12-Factor App** methodology is a set of principles for cloud-native
software; Factor III ("Config") says configuration that varies between
deployments — credentials, hostnames, bucket names — must live in env vars,
never in code or committed files. `.env.example` documents which env vars
exist (committed); `.env` holds the real values (gitignored).

### Why we use it
The pipeline runs on three machines (laptop, DGX, GPU cloud) with different
hostnames, different model selections, and different storage buckets. Hard-
coding any of those means rebuilding for every environment. Putting them in
env vars means the same Docker image runs everywhere, configured at startup.
And keeping secrets out of the repo eliminates the most common credential
leak: an accidental `git add -A` of `.env`.

### How it fits
`cfp/config.py` reads every external dependency through `os.getenv()`:
`PG_DSN`, `REDIS_URL`, `OLLAMA_HOST`, `CFP_MACHINE`, `GCS_BUCKET`,
`GCS_PREFIX`, `RCLONE_REMOTE`, `USER_AGENT` — see codegen/01 lines 25–87.
Every getenv call has a sensible local-dev default so `make run` works on a
fresh laptop.

### Further reading
*The Twelve-Factor App* (`12factor.net`, free online) by Adam Wiggins.

---

## Module 24 — Python Type Hints and Dataclasses

### What it is
**Type hints** annotate function signatures and variables with the types
they expect: `def fn(x: int) -> str:`. They are not enforced at runtime —
they're consumed by IDE autocomplete and static checkers like `mypy`. A
**`@dataclass`** is a class decorator that auto-generates `__init__`,
`__repr__`, and `__eq__` from class-level type annotations. **`@dataclass(slots=True)`**
adds `__slots__` to skip the per-instance `__dict__`, which makes attribute
access faster and shrinks memory footprint by ~30% for large collections.
**`Optional[str]`** and **`str | None`** are the same type; the second is
the Python 3.10+ syntax. **`field(default_factory=list)`** provides a fresh
mutable default per instance — a class-level `= []` would be shared across
all instances and is a classic bug.

### Why we use it
Models in this pipeline are passed through async tasks, batched into LLM
calls, serialised to JSON, and written to PostgreSQL. Without type hints,
each handoff is a guessing game; with hints, the IDE flags a typo before
the tier-2 LLM ever runs. `slots=True` matters because the embedding worker
holds tens of thousands of `Event` instances in memory at once.

### How it fits
`cfp/models.py` (codegen/01) defines `Event`, `Person`, `Organisation`,
and `Venue` as `@dataclass(slots=True)` with full type hints; `Optional[date]`
appears on every deadline field; `field(default_factory=list)` is used for
the `categories` and `raw_tags` fields.

### Further reading
*Robust Python* by Patrick Viafore; the PEP 484 (type hints) and PEP 557
(dataclasses) documents.

---

## Module 25 — Regular Expressions in Python

### What it is
A **regex** is a pattern that matches text. `re.compile(pattern)` builds a
reusable pattern object; `re.search(pattern, text)` does a one-shot match.
**Groups** `()` capture sub-matches; **non-capturing groups** `(?:...)`
group without capturing; **named groups** `(?P<year>20[0-9]{2})` give
captures readable names. **Greedy** `*`/`+` match as much as possible;
**lazy** `*?`/`+?` match as little. The `re.DOTALL` flag makes `.` match
newlines (needed for multi-line HTML).

### Why we use it
Several extraction tasks on WikiCFP are too small to justify a parser but
too repetitive to write inline: extracting a 4-digit year from text, finding
deadline date strings inside cells, and filtering non-Latin-script content.
`re.compile()` once at module load + `pattern.search(text)` per row beats
inline `re.search(...)` by ~5× because compilation is cached only when the
pattern fits in a small LRU.

### How it fits
`_NON_LATIN_RE` in `cfp/parsers/wikicfp.py` (codegen/04 lines 27–46) is
compiled once and used by `_is_english()`, which counts non-Latin characters
and returns `False` if more than 5% of the text is non-Latin. The same file
uses `re.search(r"20[0-9]{2}", text)` for year extraction in date cells.

### Further reading
*Mastering Regular Expressions* by Jeffrey Friedl (3rd ed.).

---

## Module 26 — YAML and JSON Parsing in Python

### What it is
**YAML** is a human-readable, indentation-based format for config and data.
**`yaml.safe_load(s)`** parses YAML into Python primitives; never use
`yaml.load(s)` without a `Loader=` argument — it can construct arbitrary
Python objects (including ones that execute code). **JSON** is a stricter,
machine-oriented format; `json.loads(s)` parses, `json.dumps(obj)` serialises.
LLM outputs are JSON because JSON is unambiguous and parseable; YAML's
ambiguity ("yes" vs `true`, indentation gotchas) makes it a bad choice for
machine-generated data.

### Why we use it
The `ai-deadlines` source is a community-maintained YAML file listing top AI
conferences with their deadlines. Parsing it correctly is the second-cheapest
data source the pipeline has (after WikiCFP). And every LLM tier returns
JSON, so the pipeline must be robust to slightly broken JSON: a 3-level
fallback (direct parse → fenced ```json``` extraction → outermost-brace
scan) catches >95% of model output without re-prompting.

### How it fits
`cfp/parsers/ai_deadlines.py` calls `yaml.safe_load(resp.text)` to parse the
deadlines YAML — see codegen/04 line 277. The LLM JSON fallback chain lives
in `cfp/llm/utils.py` as `_parse_json_response()` and is invoked by every
tier client.

### Further reading
The PyYAML documentation; the JSON spec (`json.org`).

---

## Module 27 — CLI Design with Python

### What it is
**`python -m cfp <command>`** runs the `cfp` package as a script — Python
imports `cfp/__main__.py` (or, when present, the `cli.py` entry point).
**argparse** is the stdlib CLI parser. **click** is a popular third-party
alternative. **typer** is a modern wrapper that builds the CLI from
type-hinted function signatures: `def run_pipeline(workers: int = 1)` becomes
a `--workers` flag automatically. **Rich** is a terminal-rendering library
for tables, coloured text, progress bars. **Exit codes** signal status to
the shell: `0` = success, non-zero = failure — what `make` and shell `&&`
chains depend on.

### Why we use it
The pipeline ships ~10 subcommands (`init-db`, `run-pipeline`, `sync-push`,
`sync-pull`, `bootstrap-ontology`, `report`, `serve`, …) and a colourful
report view (deadlines coloured red/orange/green by urgency). typer + Rich
gets that with the least code: no boilerplate parser construction, free
type validation, and the same Python functions become both library calls
and CLI entries.

### How it fits
`cfp/cli.py` (codegen/12) wires every command via typer; the `report`
subcommand uses Rich's `Table` with cell colouring (`red` if deadline < now,
`orange` if < 7 days, `green` otherwise). `make run` invokes `python -m cfp
run-pipeline`, relying on the `0` exit code to chain into `make sync`.

### Further reading
The typer documentation; *Click* docs by Armin Ronacher.

---

## Module 28 — Logging and Structured Logging

### What it is
Python's **`logging`** module provides leveled output: `DEBUG`, `INFO`,
`WARNING`, `ERROR`, `CRITICAL`. Each logger has a **name** (conventionally
`__name__` of the module), forming a hierarchy: `cfp.parsers.wikicfp`
inherits from `cfp.parsers` inherits from `cfp` inherits from root.
**Structured logging** writes each event as a JSON object with named fields
(`{"ts": "2026-04-26T12:00:00Z", "level": "INFO", "event": "scrape_ok",
"url": "https://wikicfp.com/...", "duration_ms": 312}`), which downstream
aggregators (Loki, Cloud Logging) can index without regex.

### Why we use it
`print()` is fine for a script. For a long-running pipeline that writes to
disk, fans out async tasks, and runs unattended overnight on a remote GPU,
you need: severity levels (so you can silence DEBUG in production), per-
module loggers (so you can crank `cfp.fetch` to DEBUG without drowning in
embedding logs), timestamps, and machine-parseable output. Free-text logs
fall apart at scale.

### How it fits
Every module starts with `logger = logging.getLogger(__name__)`. The pipeline's
operational dashboard reads structured log lines built around a Redis hash
`cfp:metrics:tier{N}` that tracks `ok`, `escalated`, `failed` counters per
tier — see `arch.md §S8`. Fields like `tier`, `outcome`, `model_name` flow
straight from the log to Grafana.

### Further reading
*Effective Python* by Brett Slatkin — the logging item; the Python `logging`
docs.

---

## Module 29 — rclone and Cloud Storage Operations

### What it is
**rclone** is rsync for cloud storage. One CLI talks to S3, GCS, Backblaze
B2, Azure Blob, Dropbox, and ~50 others with the same syntax. **`rclone
copy SRC DST`** is *additive* — files in DST that aren't in SRC are left
alone. **`rclone sync SRC DST`** makes DST *match* SRC, which means it
deletes files in DST that aren't in SRC. For backups always use `copy`;
`sync` will silently destroy older snapshots if used the wrong way round.
**`rclone config`** is an interactive setup that creates a named remote
(e.g. `gcs:`) bound to credentials.

### Why we use it
The pipeline's cloud-portability promise (work on any machine, sync state
to GCS) requires a tool that talks GCS without a Google-specific SDK. On
GKE, **Workload Identity** binds the pod's K8s service account to a GCP
IAM identity, so rclone calls authenticate with no JSON key file mounted —
the most common credential-leak vector vanishes.

### How it fits
`cfp/sync.py` (codegen/16) calls `rclone copy ./data/pg_backup/
gcs:cfp-data/prod/pg_backup/` after `pg_dump -F c -f latest.dump`. The
remote name comes from `RCLONE_REMOTE` in `config.py`; the bucket and prefix
from `GCS_BUCKET` and `GCS_PREFIX`.

### Further reading
The rclone documentation (`rclone.org/docs`); Google's Workload Identity
guide.

---

## Module 30 — Makefile and Build Automation

### What it is
A **Makefile** is a file of named recipes. `make run` runs the recipe under
`run:`; `make setup` runs the one under `setup:`. A target with prerequisites
(`build: src/foo.py`) runs the recipe only if the prerequisite is newer than
the target file. **`.PHONY`** declares a target as not-a-file (otherwise
`make` would skip it once a file of that name appeared in the directory).
Tabs (not spaces) introduce recipe lines — this is the most common Makefile
gotcha for newcomers.

### Why we use it
The four-phase machine lifecycle (pull → run → sync → wipe) is the right UX
target: one verb per phase, easy to remember, easy to chain. A Makefile
collapses that into `make pull && make run && make sync && make wipe` — or
the simpler `make all`. A shell script could do the same, but `make`'s
target-graph semantics (run prerequisites first, only what's needed) and
its near-universal availability make it the lowest-friction choice.

### How it fits
`Makefile` (codegen/13) declares `setup`, `run`, `sync`, `wipe`, `logs`,
`shell-pg`, `shell-redis` as `.PHONY` targets. `make wipe` runs `docker
compose down -v` — the destructive reset that's central to the ephemeral-
local-state model in Module 18.

### Further reading
*Managing Projects with GNU Make* by Robert Mecklenburg.

---

## Module 31 — The Academic Conference Ecosystem

### What it is
A **CFP** (Call for Papers) is an announcement inviting researchers to
submit their work to a conference. The typical timeline runs ~9 months:
CFP published → abstract deadline → full paper deadline → peer review →
notification → camera-ready → conference. **Peer review** is the practice
of submissions being read and scored by domain experts; **double-blind**
hides both author and reviewer identities, **single-blind** only the
reviewer's. **CORE rankings** (A\*/A/B/C) are the de-facto quality tiers
for CS conferences, maintained by the Computing Research and Education
Association of Australasia. Major **publishers** (IEEE, ACM, Springer,
USENIX) sponsor or organise conferences and publish the **proceedings** —
the bound, citable record of accepted papers. A **workshop** is a smaller
focused event co-located with a main conference. **DBLP** is a free
bibliographic database covering nearly every published CS paper.

### Why we use it
The pipeline's value is filtering signal from noise — and the academic
ecosystem provides several ready-made signals. CORE rank flags quality
venues. Publisher (IEEE/ACM/USENIX) excludes most predatory ones. DBLP
presence confirms a venue is real. Workshop-vs-main distinguishes scope.
Without these vocabulary anchors, the pipeline would drown in low-quality
listings.

### How it fits
The `rank` field on the `Event` dataclass and `events.rank` column in the
DB schema (codegen/05) hold the CORE rank. The Tier 2 LLM proposes a rank;
the CORE table import (`arch.md §S12`) overrides it when they disagree.
CORE A\* and A conferences are the pipeline's primary quality filter for
report views.

### Further reading
*Conducting Research in Computer Science* by various authors; the CORE
Conference Ranking Portal documentation.

---

## Module 32 — OWL Ontologies and Protege

### What it is
An **ontology** in the formal CS sense is a shared, machine-readable
vocabulary of a domain: a set of classes (Conference, Researcher, Topic),
properties (chairs, isAbout, partOf), and relationships (DiffusionModels
isA GenerativeAI), described in a way software can reason over. **OWL 2**
(Web Ontology Language) is the W3C standard format. Information lives as
**triples**: subject → predicate → object (e.g. `ICCV → isA → ComputerVisionConference`).
**`is_a`** means the subject is a kind of the object; **`part_of`** means
the subject is a component of the object. **owlready2** is a Python
library that loads, edits, and writes OWL files. **Protege** is the
free Stanford GUI for browsing and editing ontologies — useful for
visualising the concept hierarchy as a tree.

### Why we use it
This pipeline's *live* ontology is the AGE graph in PostgreSQL — always
current, queried directly. OWL is just an export: a snapshot in a
standard format that domain experts can open in Protege to inspect or
share. Editing in Protege wouldn't flow back, so the export is read-only
by design. This split keeps the working ontology fast and the
collaborative ontology portable.

### How it fits
`cfp/ontology.py` (v2 scope) walks the `Concept`, `IS_A`, and `SYNONYM_OF`
nodes/edges in the AGE graph via Cypher and uses `owlready2` to write
`ontology/conference_domain.owl` — see `context.md §14`. The .owl file
ships next to the repo for Protege users; the source of truth stays in
PostgreSQL.

### Further reading
*Semantic Web for the Working Ontologist* by Allemang, Hendler, and Gandon.

---

## Module 33 — pgBouncer and Connection Pooling

### What it is
A **PostgreSQL connection** is a persistent TCP socket plus an
authentication handshake — opening a fresh one takes ~10–20 ms and
~10 MB of server RAM. PostgreSQL caps total connections at
`max_connections` (default 100). **Connection pooling** keeps a small
pool of long-lived real connections and multiplexes many client
connections onto them. **pgBouncer** is a tiny C daemon that does
exactly this: clients connect to it (cheap), it routes them onto the
real PG connections. **Transaction-mode pooling** binds a real
connection to a client only for one transaction (efficient, but breaks
session-state features like `SET` and prepared statements). **Session-
mode pooling** binds for the whole session (works with everything, but
less efficient).

### Why we use it
At one Python process with five workers, raw connections are fine. At
ten Kubernetes scraper pods × five workers each = 50 connections — half
the default cap, with no headroom for dashboards, migrations, or
admin sessions. A 100th connection attempt fails with `too many
connections`. pgBouncer pushes the breaking point out by ~10×.

### How it fits
The current single-machine v1 does *not* run pgBouncer. `arch.md §S4`
flags pgBouncer as required when scraper workers scale horizontally
above ~10 concurrent connections — which happens during the K8s
migration in §5.

### Further reading
The pgBouncer documentation; *PostgreSQL High Performance* by Gregory
Smith.

---

## Module 34 — Concurrency Deep-Dive: Threads vs asyncio vs Multiprocessing

### What it is
The **GIL** (Global Interpreter Lock) is a CPython mutex that serialises
Python bytecode execution in one process — only one thread runs Python
at a time. Threads still help for I/O because the GIL is released around
blocking syscalls (a thread waiting on a socket gives up the GIL).
**asyncio** is single-threaded *cooperative* concurrency: one thread,
many coroutines, explicit `await` yield points. **multiprocessing**
spawns multiple Python processes, each with its own GIL — true CPU
parallelism. Rule of thumb: threads for blocking legacy code, asyncio
for I/O, multiprocessing for CPU.

### Why we use it
Picking the right concurrency model per task is performance-critical.
HTTP fetches and PostgreSQL writes are I/O-bound — asyncio is the right
fit. LLM inference is GPU-bound and one model fits in VRAM at a time —
sequential calls are correct, parallel would just thrash. Embedding
generation could in principle parallelise, but Ollama serialises GPU
access internally so multiprocessing wouldn't help. Knowing this avoids
"add threads, see no speedup" rabbit holes.

### How it fits
`cfp/fetch.py` opens an `aiohttp.ClientSession` and uses `asyncio.gather()`
to fan out fetches against multiple domains concurrently; per-domain
Gaussian delay still serialises within a domain. `cfp/llm/tier{N}.py`
calls Ollama sequentially. See `arch.md §S13` (async HTTP) and §S15
(`--workers N` for orchestrator-level parallelism).

### Further reading
*High Performance Python* by Gorelick and Ozsvald; David Beazley's
talks on the GIL.

---

## Module 35 — Backoff, Jitter, and Retry Patterns

### What it is
**Naive retry** (immediate retry on failure) is the bug. **Exponential
backoff** waits `BASE**attempt` seconds: 2, 4, 8, 16, … doubling each
time. **Cap** stops growth (`min(BASE**attempt, CAP)`) so you don't
wait an hour after attempt 12. **Jitter** adds random noise so workers
don't synchronise: `delay + random.random()`. **Decorrelated jitter**
is even better: `min(cap, random.uniform(base, prev_delay * 3))` —
breaks any incidental correlation between workers. **Thundering herd**
is the failure mode jitter prevents: thousands of workers all retry at
the same instant after a brief outage, re-stampeding the recovering
service. Distinguish **`RetryableError`** (try again with backoff) from
**`FatalError`** (stop trying — 404 means the page is gone).

### Why we use it
At one worker, retry strategy doesn't matter much. At 20 K8s scraper
pods all hitting WikiCFP, naive retry guarantees a self-DDoS the moment
WikiCFP burps. Exponential backoff + jitter is the textbook fix and is
cheap to implement. Distinguishing fatal from retryable means we don't
waste five attempts on a `410 Gone` URL.

### How it fits
`cfp/config.py` sets `MAX_RETRIES=5`, `RETRY_BACKOFF_BASE=2.0`,
`RETRY_BACKOFF_CAP=600` (codegen/01 lines 79–81). `cfp/fetch.py`'s
`with_retry` decorator catches `RetryableError`, sleeps
`min(BASE**attempt + random.random(), CAP)`, and after `MAX_RETRIES`
`RPUSH`es the job onto `cfp:dead` for human inspection.

### Further reading
The AWS Architecture Blog post "Exponential Backoff and Jitter" by Marc
Brooker; *Release It!* by Michael Nygard.

---

## Glossary

| Term | One-line definition |
|------|---------------------|
| 12-Factor App | Methodology for cloud-native software; mandates config in env vars, statelessness, and disposability. |
| AGE | Apache AGE — a PostgreSQL extension that adds property-graph storage and Cypher query support inside PG. |
| aiohttp | Async HTTP client/server library for Python; the asyncio replacement for `requests` used in `cfp/fetch.py`. |
| ANN | Approximate Nearest Neighbour — index-based vector search that trades 1-5% recall for 100× speed. |
| AOF | Append-Only File — Redis persistence mode that logs every write to disk; replay rebuilds in-memory state on crash. |
| async/await | Python keywords marking a coroutine and a yield point inside it; the syntactic basis of asyncio. |
| asyncio | Python's standard-library single-threaded cooperative-concurrency framework for I/O-bound work. |
| BeautifulSoup4 | Python HTML parsing library; turns raw HTML into a navigable DOM tree (`find()`, `find_all()`, CSS selectors). |
| CFAA | Computer Fraud and Abuse Act — US law making unauthorised computer access a federal offence. |
| CFP | Call for Papers — an academic announcement soliciting paper submissions for a conference or journal. |
| CMT | Microsoft's Conference Management Toolkit — a paper submission system used by many ACM and IEEE venues. |
| COALESCE | SQL function returning the first non-null argument; used in upserts to avoid wiping known fields with new NULLs. |
| CORE ranking | Australian-led conference quality ranking with tiers A*/A/B/C; authoritative for CS conferences. |
| Coroutine | A function defined with `async def` that can suspend at `await` points and resume later on the event loop. |
| Cypher | Neo4j-originated graph query language used by Apache AGE; reads as ASCII art for nodes and edges. |
| DBLP | Free bibliographic database covering virtually every published computer-science paper and venue. |
| Dead-letter | A list of jobs that failed too many times; processed by Tier 4 batch rather than discarded. |
| dateutil | Third-party Python library extending the stdlib `datetime` module; `parse(fuzzy=True)` handles messy date strings. |
| Decorrelated jitter | Backoff strategy that draws each delay from `uniform(base, prev_delay*3)`, capped — better dispersion than additive jitter. |
| DGX | Nvidia's high-end GPU server (A100/H100, 80+ GB VRAM); needed for Tier 4 deepseek-r1:70b. |
| docker compose down -v | The destructive Docker Compose reset; stops services AND removes named volumes — wipes all local state. |
| DOM | Document Object Model — the tree representation of an HTML/XML document that BeautifulSoup4 navigates. |
| DSN | Data Source Name — the URL string identifying a database (`postgresql://user:pass@host:port/db`). |
| DuckDB | An in-process columnar OLAP database used here as a read-only analytics layer over PostgreSQL. |
| EDAS | Editor's Assistant — a paper submission system used heavily in IEEE communications and networking conferences. |
| EasyChair | A widely used paper submission and conference management system, especially in academic CS. |
| Embedding | A dense numeric vector (here 768-d) representing the semantic content of a piece of text. |
| Escalation | The act of pushing a record to the next-higher LLM tier when the current tier's confidence is too low. |
| Event loop | The asyncio scheduler that runs ready coroutines and resumes suspended ones when their I/O completes. |
| Exponential backoff | Retry pattern where wait time grows as `BASE**attempt`; combined with a cap and jitter to avoid thundering herds. |
| FatalError | Project-specific exception for non-retryable conditions (404/410/robots disallow); pushes the job to dead-letter immediately. |
| field(default_factory) | Dataclass helper providing a fresh mutable default per instance (avoids the shared-mutable-default bug). |
| GCS | Google Cloud Storage — Google's object store, used here as the off-machine persistence layer. |
| GIL | Global Interpreter Lock — CPython mutex preventing true thread-parallel Python bytecode execution. |
| HNSW | Hierarchical Navigable Small World — a graph-based ANN index; higher recall than IVFFlat but bigger and slower to build. |
| IVFFlat | Inverted-File Flat — pgvector's default ANN index; clusters vectors into `lists` cells for fast lookup. |
| ISO-8601 | International date/time format `YYYY-MM-DD[Thh:mm:ssZ]` that sorts lexicographically; the project's canonical date string. |
| Inflight lease | A short-lived Redis key declaring "worker X has dequeued job Y"; auto-expires to prevent lost work on crash. |
| Jitter | Random noise added to retry delays so concurrent workers don't synchronise their retries. |
| JSON mode | LLM runtime flag that constrains sampling to produce syntactically valid JSON. |
| KEDA | Kubernetes Event-Driven Autoscaler — scales pods based on external metrics like queue depth. |
| lxml | Fast C-based XML/HTML parser used as BeautifulSoup4's preferred backend; ~3× faster than the stdlib `html.parser`. |
| Named volume | Docker-managed persistent disk area; survives container restart and `docker compose down`, wiped only by `down -v`. |
| NavigableString | BeautifulSoup4 type representing a text leaf in the DOM tree; behaves like `str` but knows its parent tag. |
| nomic-embed-text | Local-runnable 768-d embedding model from Nomic; runs on CPU or ~300 MB VRAM via Ollama. |
| os.getenv | Python stdlib function reading an environment variable with a default fallback; the 12-Factor config primitive. |
| OWL 2 | W3C Web Ontology Language standard; the format `cfp/ontology.py` exports the AGE graph into for Protege. |
| owlready2 | Python library for loading, manipulating, and writing OWL 2 ontology files. |
| pgBouncer | Connection pooler in front of PostgreSQL; needed when many app pods exhaust PG's connection limit. |
| pgvector | PostgreSQL extension adding a `vector` column type and ANN indexes (IVFFlat, HNSW). |
| .PHONY | Makefile directive declaring a target as not-a-file, so `make` always runs its recipe even if a file of that name exists. |
| Predatory conference | A conference that accepts papers with no peer review for a fee; pollutes search results if not filtered. |
| Proceedings | The bound, citable record of papers accepted at a conference; published by IEEE/ACM/USENIX/Springer. |
| Protege | Free Stanford GUI for browsing and editing OWL ontologies; consumes the .owl export from `cfp/ontology.py`. |
| psycopg3 | The current-generation Python driver for PostgreSQL; supports async, binary protocol, and native jsonb. |
| Quantisation | Compressing model weights to lower-bit representations (Q4, Q8) to fit smaller GPUs at some accuracy cost. |
| rclone | A provider-agnostic CLI for cloud storage (GCS, S3, B2, etc.); used to push pg_dump and pull state. |
| rclone copy vs sync | `copy` is additive and safe; `sync` deletes files in destination not present in source — never use `sync` for backups. |
| re.DOTALL | Regex flag that makes `.` match newline characters, needed for matching multi-line HTML chunks. |
| Redis | An in-memory key-value store with rich data structures, used here for the queue, rate limits, and inflight leases. |
| RetryableError | Project-specific exception for transient conditions (429/5xx/timeout/LLM JSON decode); the `with_retry` decorator backs off and tries again. |
| Rich (Python library) | Terminal rendering library for tables, coloured output, and progress bars; powers the CLI report's deadline colouring. |
| SETNX | "Set if Not eXists" — atomic Redis primitive used for dedup-before-enqueue and per-domain rate limit. |
| slots=True | `@dataclass` argument adding `__slots__`, replacing per-instance `__dict__` for faster attribute access and lower memory. |
| Spot node | A discounted cloud VM that the provider can reclaim with brief notice; used for GPU batch work. |
| StatefulSet | Kubernetes resource for stateful services with stable identities and per-pod storage; used for PostgreSQL/Redis. |
| Structured logging | Logging style emitting one JSON object per event (vs free text); machine-parseable by Loki, Cloud Logging, etc. |
| Thundering herd | Failure mode where many workers retry at the same instant after an outage, re-stampeding the recovering service. |
| Tier | One of the four LLM cascade levels (Tier 1=qwen3:4b, Tier 2=qwen3:14b, Tier 3=qwen3:32b, Tier 4=deepseek-r1:70b). |
| Tool calling | LLM feature where the model emits a structured "call function X with args Y" message; used in Tier 3. |
| TTL | Time To Live — a Redis key's expiry duration; underpins rate limiting, seen-URL dedup, and inflight leases. |
| typer | Modern Python CLI library that builds the parser from type-hinted function signatures; used by `cfp/cli.py`. |
| Upsert | INSERT ... ON CONFLICT DO UPDATE — atomic insert-or-update that keeps scraping idempotent. |
| WAL | Write-Ahead Log — PostgreSQL's durability mechanism; underlies replication and point-in-time recovery. |
| WikiCFP | The community-edited CFP listing site at `www.wikicfp.com`; the pipeline's primary scrape source. |
| Workload Identity | GKE feature binding a Kubernetes service account to a GCP IAM identity; removes the need for JSON key files. |
| Workshop (academic) | A smaller, focused event co-located with a main conference; usually one or two days, narrower topic. |
| yaml.safe_load | The only safe way to parse YAML in Python; `yaml.load` without a safe Loader can execute arbitrary code. |
