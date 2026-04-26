# Architecture Review — WikiCFP Conference-Knowledge Pipeline

> Senior architect review. Generated 2026-04-26.
> Companion to `context.md` (architecture spec) and `prompts.md` (LLM contracts).
> This document is opinionated. Where a recommendation is made, treat it as a default
> to argue against, not as gospel.

---

## How to read this document

- **Section 1** lists every unresolved architectural question that must be answered
  before the implementation is correct. Each question is paired with candidate
  answers and a recommendation.
- **Section 2** is the risk register — what can go wrong, how badly, and how to
  mitigate.
- **Section 3** is the formal Architecture Decision Record (ADR) log for the
  major choices already made.
- **Section 4** lists prioritised improvements above and beyond the current spec.
- **Section 5** sketches a Kubernetes target architecture, since the pull→run→wipe
  lifecycle described in `context.md §18` is naturally a Kubernetes Job.

The spec in `context.md` is authoritative. This document interprets, questions,
and extends it. Where this document and `context.md` disagree, `context.md` wins
unless an ADR supersedes it.

---

# Section 1 — Open Architectural Questions

## Q1. PostgreSQL lifecycle: managed always-on, StatefulSet+PVC, or Docker Compose?

**Why it matters.** The spec models PostgreSQL as ephemeral local state restored
from a `pg_dump` in GCS (`context.md §3, §18`). That works for a single laptop
running one session at a time. The moment you have two machines wanting to run
in parallel — or you want a 24×7 query API — the assumption breaks. `pg_dump` is
a snapshot; it loses any writes between snapshots. There is no "merge" path.

**Candidates.**

1. **Cloud SQL (always-on, managed).** A single canonical PostgreSQL is the source
   of truth. Local Docker Compose disappears. Local sessions connect over a
   private IP (or Cloud SQL Auth Proxy). Pros: no sync logic, no lost writes,
   queryable any time. Cons: ~$50/month minimum for a small instance, requires
   Apache AGE installation in Cloud SQL (Cloud SQL Postgres supports custom
   extensions only on rare flavours — AGE is **not** in the Cloud SQL extension
   allowlist as of 2026-04). Effective blocker.
2. **StatefulSet + PVC on GKE.** A long-lived persistent volume holding the
   PostgreSQL data directory. The pod can scale to zero (delete the StatefulSet)
   while the PVC keeps the data. Pros: AGE works (custom Docker image), no dump/
   restore, keeps the "wipe on shutdown" model at the volume level. Cons: PVCs
   on GKE are zonal — moving between zones costs a snapshot+restore. Cost when
   running: ~$10–15/month for a small SSD PVC even when the pod is scaled to zero.
3. **Docker Compose + GCS pg_dump (current).** Pros: simplest, zero cost when
   nothing is running, works on any laptop. Cons: cannot run two sessions
   concurrently, full-dump round-trip per session, one corrupted upload =
   total loss until next successful sync.

**RECOMMENDED.** Keep Docker Compose for **iterative development and the
single-machine path**. Add a StatefulSet+PVC variant for the GKE pull→run→wipe
job (Section 5). Cloud SQL is rejected because AGE is not supported. Do **not**
attempt to support concurrent sessions — keep the "one session at a time"
invariant from `context.md §2`. If two sessions ever need to run in parallel,
revisit and shard by source (one session scrapes WikiCFP, another scrapes IEEE)
rather than letting both write to the same DB.

---

## Q2. Dedup trigger timing: synchronous, near-real-time, or batch?

**Why it matters.** `context.md` says dedup uses "pgvector ANN + DeepSeek-R1
confirmation" and lists `dedup-sweep` as a CLI command, but never says **when**
it runs in the pipeline. If a duplicate is allowed to land in `events`, it gets
its own `event_id`, gets synced to AGE, and pollutes downstream reports. Removing
it later is expensive (orphaned graph edges, broken FKs from `event_people`).

**Candidates.**

1. **Synchronous on every Tier 2/3 write.** Before INSERT, embed the candidate,
   query the IVFFlat index for the nearest neighbour, and if cosine ≥ 0.92 ask
   DeepSeek-R1 for a yes/no. Pros: never write a duplicate. Cons: adds an LLM
   call to the hot path; on `gpu_mid` DeepSeek-R1:32b is unavailable, so you'd
   have to escalate every Tier 2 write to Tier 4 — pipeline stalls.
2. **Asynchronous "dedup queue".** After write, push the new `event_id` to
   `wcfp:dedup_pending`. A separate worker drains the queue, runs ANN + LLM,
   and merges duplicates. Pros: hot path unaffected. Cons: there's a window
   where duplicates exist; AGE sync may fire on the duplicate first.
3. **Nightly batch via `dedup-sweep`.** Recompute embeddings, scan all pairs above
   threshold, merge. Pros: simplest. Cons: a duplicate can live for a full day
   in reports.

**RECOMMENDED.** Hybrid. (a) On Tier 2/3 write, do a **cheap pgvector lookup
only** — no LLM call. If the top neighbour exceeds **DEDUP_COSINE = 0.97**
(higher than the dedup threshold), assume duplicate and skip the write entirely
(idempotent insert). (b) For 0.92 ≤ cosine < 0.97, push to `wcfp:dedup_pending`
for asynchronous LLM confirmation. (c) Run `dedup-sweep` nightly as a safety net
across the whole table. This keeps the hot path one SQL query, defers LLM cost,
and bounds duplicate-survival time.

Add a constant in `config.py`: `DEDUP_AUTO_MERGE = 0.97`.

---

## Q3. Ontology bootstrap: what is the seed?

**Why it matters.** `context.md §14` says Tier 2 builds `RawTag`–`SYNONYM_OF`–
`Concept` via pgvector clustering and Tier 3 builds `Concept`–`IS_A`–`Concept`
via co-occurrence + Qwen3:32b. But the **first** run has no `Concept` nodes —
clustering needs at least one centroid, IS_A needs at least one parent. There
is no documented seed.

**Candidates.**

1. **Seed from the `Category` enum.** The 13 enum values (`AI`, `ML`, `DevOps`,
   ...) become the initial root `Concept` nodes at depth 0/1. Every `RawTag`
   first attempts to match one of these by embedding similarity. New `Concept`s
   get created as children of these roots. Pros: deterministic, leverages the
   already-curated category list. Cons: forces every concept under one of 13
   roots, which is wrong for cross-cutting concepts ("differential privacy"
   spans Security and ML).
2. **Seed from a public ontology (e.g. CSO — the Computer Science Ontology, or
   ACM CCS).** Import 14k Computer Science Ontology nodes as `Concept`s at
   depth 1–4. Pros: realistic hierarchy on day one. Cons: license review needed
   (CSO is CC-BY); hierarchy may not match WikiCFP terminology; one-time import
   complexity.
3. **No seed — bootstrap from the first Tier 4 batch.** Run the pipeline on
   ~500 conferences, accumulate `RawTag`s, and let the first DGX Tier 4 batch
   propose the entire initial hierarchy from scratch. Pros: zero curation
   needed. Cons: requires DGX availability before any reports are generated.

**RECOMMENDED.** Option 1 with cross-linking via `RELATED_TO`. The 13 enum
values become root `Concept`s with `depth=1` (`ResearchField` is `depth=0`).
Cross-cutting concepts get one `IS_A` to their primary parent and `RELATED_TO`
edges to others. Add a one-time `bootstrap-ontology` CLI command that
idempotently inserts these 13 roots and the `ResearchField` super-root.

The first user-facing review of `IS_A` edges should happen before any are
written: Tier 4 emits proposals; a human (or a confidence threshold ≥ 0.85)
gates which become live edges. Anything 0.5–0.85 goes to a review queue
(see Suggestion 3). Below 0.5: discard.

---

## Q4. AGE consistency with PostgreSQL: derived view or authoritative store?

**Why it matters.** `context.md §13` says "After every write: `graph.py` syncs new/
updated nodes and edges to AGE." This implies AGE is downstream of the relational
tables. But `context.md §14` says "The AGE graph IS the live ontology." This
implies AGE is authoritative for ontology data. The two statements are in
tension. If a relational write succeeds and the AGE sync fails, what is the
recovery story?

**Candidates.**

1. **AGE is a derived projection of the relational tables.** Every node and edge
   in AGE is reproducible from `events`, `series`, `people`, `venues`, `orgs`,
   `event_people`, `event_organisations`. On AGE corruption: drop the graph,
   replay from SQL. Pros: single source of truth, simple recovery. Cons:
   ontology edges (`IS_A`, `RELATED_TO`) have no relational backing — they
   only live in AGE.
2. **AGE is authoritative for graph-only data; relational is authoritative for
   entity data.** Add `concepts` and `concept_edges` relational tables that
   shadow the AGE ontology. AGE becomes a derived projection of *both*. Pros:
   full recoverability. Cons: schema duplication.
3. **AGE is fully authoritative.** Drop the relational `events` table, store
   everything in AGE properties. Cons: loses pgvector JOIN, loses DuckDB
   analytics, contradicts `context.md §3`. Reject.

**RECOMMENDED.** Option 2. Add two tables:
- `concepts (concept_id, name, depth, description, created_by_tier, created_at)`
- `concept_edges (subject_id, predicate, object_id, weight, created_by_tier, confidence, created_at)`

AGE is rebuilt from these tables by a `graph rebuild` command. The sync logic
in `graph.py` writes to **both** the relational table and AGE inside a single
PG transaction (AGE is just a Postgres extension; Cypher inside `cypher()` is
transactional with the surrounding SQL). If AGE sync fails: the transaction
rolls back, the relational write does not happen, the job lands in
`wcfp:dead`. This is automatic — no separate reconciliation job needed. Add
a periodic `graph verify` command that diffs the projection against AGE and
alerts on mismatch.

---

## Q5. Dead-letter drain on small machines: who runs Tier 4?

**Why it matters.** `context.md §2` says jobs whose required model is absent
escalate to `wcfp:escalate:tier4`. If a user only ever runs `gpu_mid`, Tier 3
and Tier 4 escalations accumulate forever. Eventually `wcfp:escalate:tier4`
becomes the queue with the most items, and the user has no DGX to drain it.

**Candidates.**

1. **API fallback.** When `WCFP_MACHINE` lacks the required tier, call an
   external API (Anthropic, OpenAI, OpenRouter, Together) for that one record.
   Pros: dead-letters always drain. Cons: violates the "fully self-hosted"
   stance implicit in `context.md §4`; introduces API key management; cost
   per call; data leakage to a third party. Mitigation: gate behind an env var
   `WCFP_ALLOW_API_FALLBACK=true`.
2. **Cloud GPU rental (Lambda Labs, RunPod, Vast.ai) on demand.** When
   `wcfp:escalate:tier4` exceeds N items, spin up a temporary cloud GPU,
   `setup.sh` on it, drain, sync, terminate. Pros: stays self-hosted; cost
   only when queue is full. Cons: complex; requires cloud provider credentials;
   spin-up time ~3 min.
3. **Accept accumulation, drain manually.** User runs Tier 4 on a borrowed
   DGX once a month / quarter. Pros: zero infra. Cons: reports are stale —
   anything 0.5–0.79 confidence sits in queue for weeks.
4. **Lower-tier substitution.** `gpu_mid` runs Tier 4 on Qwen3:14b in batch
   mode, accepting reduced quality. Pros: pipeline always closes the loop.
   Cons: quality loss; muddles the tier semantics.

**RECOMMENDED.** Combine 2 and 3. Default behaviour: accept accumulation. Add
a `tier4-cloud` CLI command that provisions a spot GPU on RunPod (or similar),
runs the drain, syncs, terminates. Cost is bounded by queue size (only spin up
when items > 100). Document that on `gpu_mid` machines, Tier 4 latency is
"manual or weekly". For users with no DGX access at all, document the API
fallback option (1) with a clear privacy warning. Reject option 4 — it
silently degrades the corpus.

---

## Q6. Cross-source deduplication: when do pairs get compared?

**Why it matters.** A conference like NeurIPS 2025 may appear in WikiCFP, in a
direct CFP email scrape, in `ai-deadlines.yml`, and on the conference's own
website. The relational schema gives each its own `event_id` because the source
URLs differ. `PROMPT_DEDUP` decides whether two `Event` records are the same,
but `prompts.md` does not say which pairs feed the prompt.

**Candidates.**

1. **All-pairs nightly.** N² pairwise comparisons across the whole `events`
   table. Pros: never miss a duplicate. Cons: 10k events = 100M pairs; even
   with pgvector ANN pre-filter to top-10 neighbours, that's 100k LLM calls.
2. **ANN-driven blocking.** For each new event, query pgvector for top-K (K=5)
   neighbours; only those K become candidate pairs. Run synchronously or via
   the dedup queue (see Q2). Pros: O(N·K) calls. Cons: can miss far-apart
   embeddings (rare).
3. **Acronym + year blocking.** Group events by `(normalised_acronym, edition_year)`;
   run pairwise within each group. Pros: very cheap; matches the dedup prompt's
   own logic. Cons: misses rebrands and acronym typos.

**RECOMMENDED.** Option 2 as the primary path. Run **(2 + 3) intersection** as
a sanity check during `dedup-sweep`: compare ANN top-5 against acronym blocking
to catch one's misses. Block size is bounded so cost is predictable. The
DEDUP_COSINE = 0.92 threshold from `context.md §12` filters the ANN candidates
before LLM confirmation.

Open follow-up: When two events are flagged `same=true`, what is the merge
policy? Keep the higher-tier record? The earlier `created_at`? The one with
more populated fields? Recommend: keep the row with the highest `confidence`,
copy non-null fields from the loser, and mark the loser `superseded_by` rather
than deleting (audit trail).

---

## Q7. Crawl throughput vs politeness: estimate the total time

**Why it matters.** `context.md` mentions Gaussian(8, 2.5) human-delay per
domain. WikiCFP is one domain. Putting numbers to it:

- 13 categories × ~10 keyword-search-result pages each = 130 search pages.
- 26 A–Z series index pages + 26 journal index pages = 52 index pages.
- Each search/index page links ~30 conference detail pages = ~5,500 detail
  pages on the first run.
- All on `wikicfp.com` (one domain) → ~5,680 pages × 8 s mean delay ≈
  **12.6 hours** of pure delay, before fetch latency, parsing, or LLM time.

That is a full overnight run for the first crawl. Subsequent runs only fetch
new/changed pages (cursor in `wcfp:cursor:{source}`, seen-URL TTL of 30 days),
so steady state is much smaller — maybe 200 pages = 30 min.

**Is per-domain rate limiting granular enough?** Yes for WikiCFP (one domain).
No for the broader scrape — Tier 3 follows external links to `ieee.org`,
`acm.org`, hundreds of conference websites. Some of those may be on the same
CDN (Cloudflare), so per-domain may not be the right granularity for politeness.

**Candidates.**

1. **Keep Gaussian(8, 2.5) per domain.** Simple, well-behaved.
2. **Reduce to Gaussian(3, 1) for WikiCFP, keep 8 for others.** WikiCFP's
   `robots.txt` does not prohibit aggressive crawling; the page is small.
   Risks IP block.
3. **Token bucket: 1 request per 4 s sustained, burst of 5.** Smoother under
   bursty workload (e.g. multi-page series).

**RECOMMENDED.** Keep Gaussian(8, 2.5) for the first run (overnight is fine).
After validating no IP block, drop WikiCFP-specific delay to Gaussian(4, 1.5).
Add a per-IP-block (CIDR /24) secondary rate limiter for shared CDN cases —
cheap to compute, prevents accidentally hammering a CDN that fronts ten
conference sites. Surface the actual realised RPS in metrics (Suggestion 8)
so you can tune empirically.

---

## Q8. pgvector index strategy: IVFFlat or HNSW, and at what scale?

**Why it matters.** `context.md §6` says "create IVFFlat after 10k+ rows".
That's a heuristic that doesn't answer "and what about 100k? 1M? when do I
switch to HNSW?"

**Numbers.**

| Index   | Build time @ 100k | Recall @ default | Memory     | Insert cost |
|---------|-------------------|------------------|------------|-------------|
| none    | 0                 | 100% (exact)     | 0          | 0           |
| IVFFlat | ~30 s             | ~95% with `lists=sqrt(N)` | low (~1× data) | cheap |
| HNSW    | ~5 min            | ~98% with `m=16, ef_construction=64` | 2-3× data | expensive |

**Candidates.**

1. **No index up to 50k events.** Sequential scan with cosine on 768-d × 50k
   rows is ~150 ms — acceptable for batch dedup, painful for interactive.
2. **IVFFlat from 10k to ~500k events.** Default. Re-build with
   `lists = floor(sqrt(rows))` whenever row count doubles.
3. **HNSW above 500k events.** Better recall and concurrent query throughput
   at the cost of slower writes.

**RECOMMENDED.** IVFFlat is correct for the realistic ceiling of this project
(estimate: 50k–200k events over its lifetime — there are not infinitely many
academic conferences). HNSW is overkill. The migration trigger is not a row
count but a query-latency SLO: if interactive embedding similarity queries
exceed 100 ms p95, switch. Document the rebuild command:

```sql
DROP INDEX IF EXISTS event_embeddings_vec_ivf;
CREATE INDEX event_embeddings_vec_ivf
  ON event_embeddings USING ivfflat (vec vector_cosine_ops)
  WITH (lists = (SELECT GREATEST(100, FLOOR(SQRT(COUNT(*)))::int) FROM event_embeddings));
```

Run after every full sync (after `pg_restore`). IVFFlat needs `ANALYZE` to be
useful — the rebuild includes implicit analyse. Track `pg_stat_user_indexes`
to confirm the index is being used.

---

## Q9. Kubernetes vs Docker Compose: stepping stone or terminus?

**Why it matters.** Docker Compose is pinned in `context.md §15`. Nothing in
the spec contemplates K8s. But the Redis inflight-lease design (`wcfp:inflight:
{job_id}` with TTL) is the textbook pattern for crash-safe horizontal worker
scaling. If you ever want to scrape 50 sources in parallel, you need K8s. If
you only ever want to scrape WikiCFP overnight on a laptop, you don't.

**Candidates.**

1. **Docker Compose is the terminus.** Simple, works. Cap at one machine.
2. **K8s is the terminus, Compose is local-dev only.** Same Docker images,
   different orchestration. Future-proof.
3. **Both supported permanently.** Same images, `docker-compose.yml` and
   `k8s/` manifests both maintained. Highest engineering cost.

**RECOMMENDED.** Option 3, with K8s manifests written **alongside** the Compose
file from the start of v1.0 (not retro-fitted). The investment is small —
one StatefulSet for PG, one Deployment for Redis, one Job for the pipeline
runner — and it preserves optionality. Spec the K8s side now (Section 5);
implement when there's a use case (multi-source parallel scrape, scheduled
GKE runs to free local hardware).

---

## Q10. Ollama model storage: persistent volume or pre-baked image?

**Why it matters.** `gpu_large` profile pulls Qwen3:32b (~20 GB) + DeepSeek-R1:32b
(~20 GB) + Qwen3:14b (~9 GB) + Qwen3:4b (~2 GB) + nomic-embed-text (~0.3 GB).
Total: ~51 GB. At a sustained 500 Mbps that's ~14 minutes of download per
session. On a 100 Mbps connection: ~70 minutes. The pipeline finishes scraping
in ~12 hours; spending 14 minutes on model pull is acceptable but not ideal.
On Kubernetes (scale-to-zero), every job pays this cost.

**Candidates.**

1. **Pull every session.** Current behaviour. Wastes bandwidth and time.
2. **Persistent Ollama volume (`/root/.ollama`).** Mounted across runs. Pros:
   one-time pull. Cons: in K8s a PVC is zonal; sharing across nodes needs a
   ReadWriteMany volume (Filestore on GCP, ~$60/month minimum for 1 TB).
3. **Pre-baked Docker image with models embedded.** Build a `wcfp/runner-gpu_large`
   image with all 51 GB of models in `/root/.ollama`. Image is pulled once per
   node, cached locally. Pros: no PVC, no shared filesystem. Cons: image is
   massive (~52 GB compressed → ~50 GB on disk per node); slow `docker pull`
   on first use; expensive private registry storage.
4. **Hybrid: persistent volume on developer laptops, pre-baked image for K8s
   spot nodes.** Local dev never re-pulls; cloud nodes get a fat image cached
   per-node by the kubelet (one pull per node, persists across pods). Pros: fast
   on both. Cons: two artefact pipelines.

**RECOMMENDED.** Option 4. For local Docker Compose: declare a named volume
`ollama-models:/root/.ollama`. The first run pulls; subsequent runs reuse. For
GKE: build a per-profile Docker image (`wcfp/runner:gpu_large-2026.04`) with
models pre-baked. Tag images by profile and date so node caches stay warm
across job runs. Use GCR's regional artefact registry (network within zone is
free). Trigger a re-bake when Ollama model versions update (rare).

---

## Q11. Single point of contention: Redis as operational store

**Why it matters.** `context.md §3` declares Redis "owns ZERO persistent business
data". But `wcfp:cursor:{source}` is the resume cursor for the entire pipeline.
If Redis loses that key, you re-scrape from page 1 of every WikiCFP search —
expensive but recoverable. If Redis loses `wcfp:dead`, you silently drop failed
jobs and never know. The escalation queues are the same: stored only in Redis,
zero durability.

**Candidates.**

1. **Status quo, accept loss.** Redis crashes are rare; pipeline is idempotent.
2. **Enable Redis AOF persistence.** Append-only file, fsync every second.
   Crash recovery loses ≤1 s of operations.
3. **Mirror critical keys to PostgreSQL.** `wcfp:dead` and `wcfp:escalate:*` are
   regularly drained to a `pg.escalations` table; cursor is upserted to
   `pg.scrape_cursor` after each batch.

**RECOMMENDED.** Option 2 + targeted mirroring (subset of 3). Enable AOF in
the Compose `redis:7-alpine` (`redis-server --appendonly yes`). For the cursor
specifically, persist to `sites.last_cursor` after every page batch (already
schemaless-friendly because `sites` exists per `context.md §6`). Dead-letter
items get a one-way drain to PG: Tier 4 reads from `wcfp:dead`, processes, and
stores results in PG; a missed batch is recoverable from `wcfp:dead` history
because Tier 4 writes its own audit row.

---

## Q12. JSON-mode failure: what happens when the LLM returns malformed JSON?

**Why it matters.** Every prompt in `prompts.md` ends with "Output ONE JSON
object only. No prose, no code fences." Local quantised models are imperfect
JSON emitters. `context.md §15` lists `RetryableError` for "LLM JSON decode" but
does not specify retry budget or behaviour.

**Candidates.**

1. **Retry the same prompt up to N times.** Pros: simple. Cons: same model +
   same prompt → same output usually; just wastes time.
2. **Repair locally (json5, demjson3, regex strip code fences).** Pros: catches
   most cases; no extra LLM calls. Cons: silent failures when repair "succeeds"
   on wrong data.
3. **Retry with temperature increased.** Pros: changes the output. Cons:
   semantic drift.
4. **Retry one tier higher.** If Tier 1 emits invalid JSON, escalate to Tier 2.
   Pros: structured fallback. Cons: amplifies cost on a model bug.

**RECOMMENDED.** 2 first (cheap), then 4 (escalate on persistent failure). One
retry only at the same tier with a "your previous output was not valid JSON,
emit only valid JSON" preamble. After two same-tier failures: escalate. Track
the parse-failure rate per model in `wcfp:metrics:parse_fail:{model}`. If it
exceeds 1%, flag for prompt review.

---

## Q13. Ontology graph: when does Cypher get expensive?

**Why it matters.** Apache AGE Cypher queries on PG are not free. The power
queries in `context.md §5` use unbounded `*0..` traversals. As `Concept`–`IS_A`
–`Concept` deepens (estimated ceiling: 5 levels), and as `Conference`–
`CLASSIFIED_AS` edges grow (~5/event × 50k events = 250k edges), traversal
cost grows.

**Candidates.**

1. **Trust AGE.** Premature optimisation.
2. **Cap `IS_A*0..` to `*0..6`.** Practical ceiling for any ontology depth.
3. **Materialise transitive closures into a relational table.** A nightly job
   computes `concept_descendants(ancestor_id, descendant_id)` once. Queries
   become a JOIN, not a graph walk. Pros: fast. Cons: stale until next refresh.

**RECOMMENDED.** Option 2 in queries (cheap defensive measure), and Option 3 as
an optimisation when the graph grows past 5k `Concept` nodes. Don't ship 3 on
day one. Track Cypher query latency in `wcfp:metrics:cypher_p99` — if >500 ms,
materialise.

---

## Q14. Quantisation strategy: are we silently degrading on small VRAM?

**Why it matters.** `gpu_small` runs Qwen3:4b on 4 GB VRAM. Ollama defaults
quantise such fits to Q4_0 or Q4_K_M. Q4 quality on JSON-output tasks is
noticeably worse than Q8. The spec does not specify a quantisation policy.

**Candidates.**

1. **Take Ollama's default.** Whatever fits.
2. **Pin quantisation per profile.** `gpu_small` uses `qwen3:4b-q4_K_M`,
   `gpu_mid` uses `qwen3:14b-q5_K_M`, `gpu_large` uses `qwen3:32b-q4_K_M` (still
   fits 24 GB), `dgx` uses `qwen3:32b-q8_0`.
3. **Always use the highest quant that fits.** Determined at startup.

**RECOMMENDED.** Option 2 — pin in `PROFILE_MODELS`. Add a quality regression
test (10 known-good Tier 1 examples; measure JSON validity + field correctness)
that runs on profile change. Document the expected accuracy delta in
`context.md`.

---

## Q15. `is_workshop` versus `Workshop` graph node: data model duplication

**Why it matters.** `context.md §5` lists `Workshop` as a separate node label,
but the relational schema in `context.md §6` does not have a `workshops` table —
workshops appear to be `events` rows with `is_workshop=true`. Are workshops
first-class entities or a flag on events?

**Candidates.**

1. **`is_workshop=true` flag only; no separate node.** `Workshop` graph label
   is dropped. Simpler.
2. **Workshop is a separate entity with its own ID.** Has the `CO_LOCATED_WITH`
   relationship to its host conference. Better matches reality (many workshops
   have their own websites, chairs, deadlines).

**RECOMMENDED.** Option 1 for v1. Workshops are events with `is_workshop=true`
and an optional `parent_event_id` (FK self-reference). Drop the standalone
`Workshop` label from `context.md §5` to remove ambiguity. Revisit if workshops
turn out to need richer modelling than events.

---

# Section 2 — Risk Register

Severity = Probability × Impact, mapped to {Low, Medium, High, Critical}.

| # | Risk | Category | Probability | Impact | Severity | Mitigation |
|---|------|----------|-------------|--------|----------|------------|
| R1 | Apache AGE extension breaks across PostgreSQL version upgrades; AGE is third-party and lags PG releases | Technical | Medium | High | **High** | Pin `apache/age:PG16_latest` digest, not tag, in compose. Subscribe to AGE GitHub releases. Maintain a "graph-rebuild from relational tables" script (Q4 ADR) so we can survive losing AGE data. Smoke test on every image bump. |
| R2 | WikiCFP IP block on aggressive crawl (~5,680 first-run requests) | Operational | Medium | High | **High** | Stick to Gaussian(8, 2.5) on first run. Honour robots.txt strictly. Set User-Agent to a contactable email. If blocked: pause 24 h, halve rate. Have a residential proxy service (BrightData, Smartproxy) as a documented fallback only — last resort. |
| R3 | pgvector IVFFlat recall degrades as data grows; dedup misses become silent | Data Quality | Medium | Medium | **Medium** | Track recall: monthly benchmark against a small held-out set of known duplicates. Rebuild index with new `lists` after every doubling. Switch to HNSW if recall drops below 90%. |
| R4 | GCS sync corruption: pg_dump uploaded mid-write or partially; restore on next session loses data | Operational | Low | Critical | **High** | Two-phase upload: write `pg_backup/staging.dump`, validate via `pg_restore --list`, then `gsutil mv` to `pg_backup/latest.dump` atomically. Keep N=7 versioned backups (`gs://bucket/pg_backup/2026-04-26.dump`). Versioning enabled on the bucket. Run `pg_restore --list` smoke test before every upload completes. |
| R5 | GKE spot GPU node preempted mid-job; in-flight Tier 3/4 work lost | Operational | High | Medium | **High** | Redis inflight lease (TTL=600s) automatically returns the job to the queue. Idempotent writes via `INSERT ... ON CONFLICT`. Tier 4 batch checkpoints progress every 10 records to PG. Document: spot preemption is expected, not exceptional. Use 2× CPU on-demand pool for orchestrator, spot only for GPU workers. |
| R6 | Ollama model quantisation degrades JSON validity on `gpu_small`/`cpu_only` | Data Quality | Medium | Medium | **Medium** | Pin quantisation per profile (Q14). Run weekly accuracy regression on a held-out set. If Tier 1 JSON-validity drops below 95%, upgrade default quant or escalate more aggressively to Tier 2. |
| R7 | Dead-letter accumulation on `gpu_mid`-only deployments — Tier 4 never runs | Operational | High | Medium | **High** | Document the `tier4-cloud` escape hatch (spot GPU rental — Q5). Surface queue depth in health endpoint (Suggestion 2). Alert when `wcfp:escalate:tier4` exceeds 500 items. |
| R8 | AGE/PG drift: relational write succeeds, AGE sync fails, no detection | Data Quality | Low | High | **Medium** | Wrap both writes in a single PG transaction (AGE Cypher runs in PG, so this is naturally transactional — verify the Python adapter's autocommit settings). Add `graph verify` periodic check (Q4 ADR). |
| R9 | Predatory or spam conferences pass Tier 1 triage and pollute reports | Data Quality | High | Medium | **High** | New `PROMPT_QUALITY_GUARD` prompt (added to `prompts.md`) gates writes on suspicious patterns. Domain blocklist (Suggestion 10). Warn-flag items for human review rather than silently writing. |
| R10 | Cross-source duplicates pollute reports and confuse rank/category aggregation | Data Quality | High | Medium | **High** | Q2 + Q6: synchronous high-threshold ANN check, async LLM confirmation, nightly sweep. Track dedup precision/recall on a labelled set. |
| R11 | CORE rank misidentified — journal rank applied to a conference (or vice versa) | Data Quality | Medium | Medium | **Medium** | `PROMPT_TIER2` already says "Never guess from prestige." Add a CORE rank cross-check: separate `core_ranks` table imported from CORE Portal once, joined at write time on `(acronym, year)`. Drop LLM-emitted ranks that disagree with the canonical CORE table. |
| R12 | India state misclassification (Hyderabad → Telangana since 2014, but historical conferences were "Andhra Pradesh") | Data Quality | High | Low | **Medium** | Ship a curated `india_cities.csv` lookup table (city → state, with effective_year if needed). LLM emits city; SQL trigger normalises to state. Override LLM-emitted `india_state` on conflict. |
| R13 | Cost overrun: GKE spot GPU node fails to scale down after job completion | Cost | Medium | High | **High** | Use Kubernetes Job (not Deployment) for the pipeline runner — terminates on completion. KEDA + cluster-autoscaler scale node pool to zero on idle. Set GKE node-pool `--enable-autoscaling --min-nodes=0`. Cost guardrail: GCP Budget alert at $50/day. |
| R14 | Security: GCS credentials exposed in env vars or container image layers | Security | Medium | Critical | **High** | Use GKE Workload Identity (no JSON key file in image). On laptops, use `gcloud auth application-default` (short-lived OAuth tokens), not service-account JSON. Forbid keys in `.env`; lint with detect-secrets pre-commit hook. Rotate any key leaked. |
| R15 | LLM hallucination of `official_url` despite "do not invent" instruction | Data Quality | Medium | Medium | **Medium** | Validate URL post-extraction: HEAD request must return 2xx within 5 s, host must not be `wikicfp.com`. Reject and re-extract. The new `PROMPT_QUALITY_GUARD` flags `invented_url`. |
| R16 | Disk exhaustion from Ollama models (51 GB on `gpu_large`) on a small laptop | Operational | Medium | Low | **Low** | Document min disk in setup. On `wipe` phase, suggest `ollama rm` for unused models. Track `ollama list` size in setup output. |
| R17 | DuckDB postgres_scanner version mismatch with pgvector / AGE types | Technical | Low | Medium | **Low** | Pin `duckdb>=1.0` and a known-good `postgres_scanner` extension version. Smoke test in CI: SELECT from a vector column via DuckDB. |
| R18 | Tier 4 prompt order-preservation contract violated — array length differs | Data Quality | Low | High | **Medium** | `PROMPT_TIER4` already requires identical input/output array lengths. Validate at runtime: if `len(out) != len(in)`, fail loudly, send the whole batch to dead-letter for re-processing one-at-a-time. Never silently truncate. |

---

# Section 3 — Architecture Decision Records

## ADR-1: PostgreSQL 16 + pgvector + Apache AGE for the source-of-truth store
**Status**: Accepted
**Date**: 2026-04-26
**Context**: The project needs vector search (semantic dedup), graph traversal
(ontology, PC chair networks), and structured queries (filter by country/date)
**in the same query**. Operating three separate stores would double-write data
and prevent server-side JOINs across modalities.

**Decision**: Use a single PostgreSQL 16 instance with two extensions: pgvector
for embeddings and Apache AGE for property-graph + Cypher. The Docker image
`apache/age:PG16_latest` ships both pre-installed.

**Consequences**:
- (+) One transaction spans relational, vector, and graph writes.
- (+) Cross-modal queries (filter by SQL, rank by cosine, traverse by Cypher)
  are expressible in one statement (`context.md §5` end).
- (+) `pg_dump` is the complete backup.
- (-) Apache AGE is third-party — version skew risk (R1).
- (-) Cloud SQL does not support AGE; we lose the option of a managed PG.
- (-) Cypher in PG syntax is awkward (`SELECT * FROM cypher(...)`).

**Alternatives rejected**:
- **Neo4j (graph) + Qdrant (vectors) + PostgreSQL (relational)**. Three stores,
  three back-ups, no cross-modal joins.
- **Neo4j alone**. No native vector search at the time of writing; no proper
  relational/SQL.
- **MongoDB**. No graph traversal, weak analytics, mediocre vector support.

---

## ADR-2: DuckDB as analytics-only layer reading PostgreSQL via postgres_scanner
**Status**: Accepted
**Date**: 2026-04-26
**Context**: Markdown reports require GROUP BY, window functions, and full-table
scans across `events` (potentially 50k+ rows). PostgreSQL is row-oriented;
analytical scans are slow there. Spinning up a separate analytical store
(Spark, BigQuery) would mean another dataset to keep in sync.

**Decision**: DuckDB attaches to PostgreSQL via the `postgres` extension in
read-only mode. All `generate_md.py` queries run in DuckDB. DuckDB never owns
local data — it is a calculation layer.

**Consequences**:
- (+) Columnar OLAP performance on the same data PostgreSQL holds — no copy.
- (+) Nothing to back up for analytics.
- (+) Standard SQL, well-known by analysts.
- (-) Reads always cross a network boundary (insignificant on localhost).
- (-) DuckDB's PostgreSQL scanner cannot push down all predicates; some
  queries fetch more than they need.
- (-) DuckDB doesn't natively understand pgvector; vector ops still happen
  PG-side via `<=>` and joined into DuckDB output.

**Alternatives rejected**:
- **Spark / Trino / DBT**. Massive overkill at this scale.
- **PostgreSQL alone**. Row-oriented; aggregation latency unacceptable for the
  full reports.
- **Maintain a separate Parquet warehouse**. Sync cost, staleness.

---

## ADR-3: Redis for queue, rate limiting, and operational state only
**Status**: Accepted
**Date**: 2026-04-26
**Context**: The pipeline needs (a) a priority queue with O(log N) push/pop,
(b) per-domain rate limiting with TTL semantics, (c) atomic dedup before
enqueue, (d) crash-safe in-flight job tracking with auto-expiry. No single
other system does all four cheaply.

**Decision**: Use Redis 7 for queue, rate limit, dedup cache, and inflight
leases. Persistence is AOF (Q11). Redis owns no business data; wiping it
loses no facts, only operational state.

**Consequences**:
- (+) Native data structures match each need (sorted set, SETNX with TTL, list).
- (+) Sub-millisecond latency.
- (+) Tiny operational footprint (one container).
- (-) AOF flush on every operation costs ~10% throughput (acceptable).
- (-) Redis becomes a single point of in-process state — but per `context.md §3`
  it is recoverable from PostgreSQL cursors.

**Alternatives rejected**:
- **RabbitMQ**. No native rate limiting, no native dedup primitive (SETNX), no
  TTL keys. We'd build all three on top.
- **Celery**. Tied to a specific broker; opinions about workers and result
  storage we don't share. Heavy.
- **AWS SQS**. Cloud-only, no per-domain rate limiter primitive, opaque.
- **Kafka**. Wrong tool — log-replay semantics, not work-queue semantics.
  Operationally heavy.
- **PostgreSQL `LISTEN/NOTIFY` + SKIP LOCKED**. Possible but requires
  hand-rolling rate limit + TTL dedup; loses the simple Redis ergonomics.

---

## ADR-4: Four-tier LLM cascade (Qwen3 4b → 14b → 32b → DeepSeek-R1 70b)
**Status**: Accepted
**Date**: 2026-04-26
**Context**: A single large model (DeepSeek-R1:70b) is overkill for ~80% of
records, which are already well-structured WikiCFP entries. A single small
model misses ambiguous cases. The cost-vs-accuracy curve has a knee around
qwen3:14b for this domain.

**Decision**: Cascade triage → extraction → tool-calling → batch curation
across four model sizes. Records escalate when confidence < threshold for
the current tier. Threshold: 0.85 / 0.85 / 0.80 / final.

**Consequences**:
- (+) ~80% of records resolve at Tier 1 (low cost, high throughput).
- (+) Hard cases get expensive treatment; easy ones do not.
- (+) Per-machine profiles map cleanly: small machines run Tier 1 only.
- (-) Four prompts to maintain, four sets of validation logic.
- (-) Escalation queues add operational complexity.
- (-) Confidence calibration is a known weakness of LLMs; thresholds may need
  tuning.

**Alternatives rejected**:
- **Single small model (qwen3:14b) for everything**. ~10% degradation in hard
  cases; we'd silently emit wrong dates and ranks.
- **Single large model (DeepSeek-R1:32b) for everything**. Latency and VRAM cost;
  excludes `gpu_small`/`gpu_mid` machines from any productive work.
- **GPT-4 / Claude API for everything**. Privacy (CFP scraping is public, but
  pipeline metadata is ours), cost (~$1k/month at expected throughput),
  vendor lock-in, internet dependency.

---

## ADR-5: Qwen3 family for tool calling, DeepSeek-R1 for pure reasoning
**Status**: Accepted
**Date**: 2026-04-26
**Context**: Some pipeline steps need tool calls (extract_text, find_links,
classify_category) for unknown external sites. Others need long, pure
reasoning chains (dedup yes/no, ontology IS_A inference). The two demands
have different model strengths.

**Decision**: Qwen3 (4b/14b/32b) handles all tool-calling tiers (1–3).
DeepSeek-R1 (32b/70b) handles only Tier 4 batch reasoning and dedup. Tools
are never offered to DeepSeek-R1.

**Consequences**:
- (+) Each model used in its strongest mode.
- (+) DeepSeek-R1's <think> traces are not wasted on simple extraction.
- (+) Qwen3's tool-calling format is reliable in Ollama.
- (-) Two model families to track, two sets of prompts to maintain.

**Alternatives rejected**:
- **Llama 3.1 / Mistral as a unified model**. Tool calling support is less
  reliable in Ollama.
- **Hermes / Functionary fine-tunes for tools**. Smaller community; risk of
  abandonment.
- **Use DeepSeek-R1 with simulated tool calling**. Possible (parse model output
  for tool invocations) but fragile and slow.

---

## ADR-6: GCS + pg_dump for off-machine persistence
**Status**: Accepted
**Date**: 2026-04-26
**Context**: The single-machine model (`context.md §2`) requires a durable
off-machine store between sessions. The store must be cheap, support large
binary blobs (50–500 MB), and integrate with `rclone`/`gsutil`.

**Decision**: GCS bucket with `pg_dump -F c` as the canonical artefact. `rclone`
handles the upload. Bucket has versioning enabled for at least 7 versions.

**Consequences**:
- (+) Cheap (~$0.02/GB/month).
- (+) Single artefact restores the entire database.
- (+) Versioning lets us roll back a bad session.
- (-) Full dump every session — at 10 GB this becomes a 30 s upload (fine);
  at 100 GB it becomes 5 min and a serious bandwidth tax.
- (-) Recovery point objective is "last successful sync", not "last write".

**Alternatives rejected**:
- **Always-on managed Postgres (Cloud SQL)**. Rejected because Cloud SQL does
  not support Apache AGE.
- **S3 / B2**. Cheaper egress on B2; but GCS is local to GKE for the K8s
  future, which makes Workload Identity simpler.
- **Logical replication to a cloud follower**. Premature complexity at current
  scale; consider when DB > 50 GB (Suggestion 5).

---

## ADR-7: nomic-embed-text for 768-d embeddings
**Status**: Accepted
**Date**: 2026-04-26
**Context**: We need a local-runnable embedding model that produces stable
vectors for dedup and concept clustering. It must work on CPU (for `cpu_only`
machines) and not change vectors across runs.

**Decision**: `nomic-embed-text` via Ollama. 768 dimensions. Runs everywhere
(~300 MB VRAM or CPU).

**Consequences**:
- (+) Open weights, deterministic with fixed seed.
- (+) 768-d is a reasonable balance of recall vs storage (50k events × 768 ×
  4 bytes = ~150 MB).
- (+) Available in Ollama on every profile.
- (-) Model updates would invalidate all stored vectors — a re-embedding job
  is required.
- (-) Quality below state-of-the-art proprietary embeddings (OpenAI ada-002,
  Cohere embed-v3).

**Alternatives rejected**:
- **OpenAI ada-002 / text-embedding-3-small**. API dependency, cost, privacy.
- **sentence-transformers (e.g. all-MiniLM-L6-v2, 384-d)**. Smaller dim is
  faster but less accurate; running outside Ollama adds another runtime.
- **BGE-M3**. Excellent quality but 1024-d; we'd need to widen all vector
  columns (not a blocker, but no clear win).

---

## ADR-8: Single-machine operation as the default deployment model
**Status**: Accepted
**Date**: 2026-04-26
**Context**: The user is one person (research/personal use). Always-on
infrastructure is wasteful; weekly cron jobs running on a laptop are sufficient.
Kubernetes from day one would be over-engineered.

**Decision**: One machine at a time runs the pipeline. `WCFP_MACHINE` controls
which models load. State persists in GCS between sessions. No central
coordinator.

**Consequences**:
- (+) Zero idle cost.
- (+) Anyone with Docker + Ollama can clone and run.
- (+) Local laptops, borrowed DGX, cloud GPU rentals all work uniformly.
- (-) No concurrent sessions — strict invariant.
- (-) Stale data between sessions (recovery point = last sync).
- (-) Manual orchestration (cron on a laptop) is fragile.

**Alternatives rejected**:
- **Always-on cluster**. Wasteful for a personal-use pipeline. Reconsider if
  multiple users emerge.
- **Kubernetes from day one**. Solves problems we don't have. Spec the future
  K8s migration (Section 5) but don't ship it now.

---

# Section 4 — Suggestions and Recommendations

Priority key: **MVP** = ship before v1.0. **Later** = post-v1.0. **Optional**
= consider, not blocking.

## S1. Workflow orchestration (MVP)

**What.** Pick an explicit DAG runner. The pipeline is:
`scrape → tier1 → tier2 → tier3 → tier4 → graph_sync → dedup → reports`. Today
this is wired together by Redis queues — every escalation is a queue push, every
worker is an asyncio task. That works for one machine; it is opaque under
failure.

**Why.** Without a DAG runner you cannot answer: "what's the state of run
2026-04-26?" "did Tier 2 finish for batch X?" "what are the in-flight tasks?"
Visibility is currently zero.

**Options compared.**

| Tool | Footprint | K8s native | Local-friendly | Best for |
|------|-----------|------------|----------------|----------|
| Prefect | Medium (server + agent) | Yes (helm chart) | Yes (cloud free tier) | Pythonic DAGs, low ceremony |
| Temporal | Heavy (db + servers) | Yes | No (overkill locally) | Long-running, durable workflows |
| Argo Workflows | K8s-native | Yes | No (needs K8s) | Already-K8s shops |
| asyncio + a state table in PG | None | n/a | Yes | What you have today, plus visibility |

**Recommendation.** **Prefect** for v2 if the K8s migration happens. For v1 on
single-machine: stay with asyncio + Redis but add a `pg.pipeline_runs` table:
`(run_id, started_at, finished_at, machine, stage, count_ok, count_escalated,
count_failed)`. Each stage updates a row. This gives visibility without a DAG
engine. The code-level change is ~50 LOC.

**When.** MVP for the `pipeline_runs` table. Prefect: Later, when K8s arrives.

---

## S2. Health-check endpoint (MVP)

**What.** A small FastAPI app on port 8080 exposing `/health`, `/metrics`,
`/queue`, `/runs`. Reads counters from Redis and rows from `pg.pipeline_runs`.

**Why.** Currently you SSH to the machine and `redis-cli LLEN wcfp:queue` to
know if the pipeline is alive. That doesn't scale to "did the cron run last
night?" Add Prometheus-format metrics for free.

**Endpoints (suggested).**

```
GET /health         {"status": "ok|degraded|down", "since": iso8601}
GET /metrics        Prometheus text format
GET /queue          {"queue_depth": int, "inflight": int, "dead": int,
                     "escalate_tier2": int, "tier3": int, "tier4": int}
GET /runs?limit=10  recent pg.pipeline_runs rows
```

**When.** MVP. Slot into `wcfp/cli.py serve --port 8080` so it doesn't add a
new container.

---

## S3. Human-in-the-loop review CLI / UI (MVP for CLI, Later for UI)

**What.** Records with confidence 0.5–0.79 currently land in dead-letter and
get drained by Tier 4, which marks them `final=true`. But a human should look
at borderline records before they enter reports — especially predatory
conferences and journal-not-conference cases caught by `PROMPT_QUALITY_GUARD`.

**Why.** Tier 4 isn't infallible. The prompt asks the model to reconcile,
but on contradictory inputs the model picks one branch and emits high
confidence. A human can resolve in seconds what the model can't.

**Recommendation.** Add a `pg.review_queue` table with rows `(event_id,
reason, severity, created_at, reviewed_by, decision, reviewed_at)`. Add CLI
commands:

```
python -m wcfp review list --reason predatory_publisher
python -m wcfp review approve <event_id>
python -m wcfp review reject  <event_id> --reason <text>
```

A simple web UI (Streamlit, ~100 LOC) reading from `pg.review_queue` is
**Later** — only worth it if review volume exceeds ~20 items/week.

**When.** MVP for the table + CLI. UI: Later.

---

## S4. pgBouncer for connection pooling (Later)

**What.** PgBouncer in transaction-pooling mode in front of PostgreSQL.

**Why.** Today: one Python process, ~5 connections. Fine. In K8s with KEDA-
scaled scrapers (Suggestion 7): 20 scrapers × 5 connections = 100; PG default
`max_connections=100` is exhausted. Connections are heavyweight in PG (~10 MB
each).

**When.** Trigger on the K8s migration. Not needed today.

**Caveat.** Apache AGE Cypher uses session-level state; transaction-pooling
mode may break Cypher across statements. Test before deploying — may need
session-pooling mode (less efficient).

---

## S5. Incremental sync to GCS via WAL archiving (Later)

**What.** Replace `pg_dump` full snapshot with `pg_basebackup` + WAL archive.
Each session ships only the WAL since the last sync.

**Why.** Full dumps are fine ≤10 GB. Above that, dump+upload time becomes
significant and bandwidth wasteful.

**Trigger.** When `pg_dump` size exceeds 10 GB or upload time exceeds 5 min.
At ~50k events with 768-d vectors and full HTML caches, expect ~5 GB. Above
500k events: definitely matters.

**When.** Later. Plan for it; don't build it now.

---

## S6. Kubernetes migration path (Later, but spec it now — Section 5)

**What.** Concrete steps to move from Docker Compose to GKE.

**Migration order (low-to-high risk).**
1. Containerise the runner (`Dockerfile.runner`) — no behaviour change.
2. Push images to GCR.
3. K8s manifests: PG StatefulSet, Redis Deployment, Ollama StatefulSet (with
   GPU node selector), runner Job.
4. GKE Workload Identity for GCS access.
5. KEDA autoscaling for scrapers.
6. CronJob for weekly pipeline run.

**What can stay as-is.** All Python code. The `WCFP_MACHINE` env var
becomes a node selector + a runtime label. `setup.sh` becomes a Job init-
container.

**What needs new code.** The runner needs to handle SIGTERM (spot preemption);
checkpoint progress before exit; resume on restart. ~100 LOC change in
`pipeline.py`.

**When.** When weekly cron on a laptop becomes annoying.

---

## S7. KEDA autoscaling for scraper workers (Later)

**What.** Kubernetes Event-Driven Autoscaler scales scraper pods based on
`LLEN wcfp:queue`.

**Why.** Today: one scraper, sequential. With KEDA: 0 pods when queue empty,
N pods when queue has N×100 items. Each pod takes a fair share of the queue
via Redis BRPOPLPUSH.

**Watch out.** Per-domain rate limit is global (Redis SETNX); doesn't scale
with worker count for the same domain. KEDA helps when you have many domains
(Tier 3 follows external links across hundreds of conference websites). It
does **not** help WikiCFP-only scraping — that's still bottlenecked at one
domain.

**When.** Later. Predicate: Tier 3 external-site scraping becomes the
bottleneck.

---

## S8. Observability stack (Prometheus + Grafana) (MVP for metrics, Later for dashboard)

**What.** The pipeline emits Prometheus metrics; Grafana visualises.

**Metrics that matter (in priority order).**

```
wcfp_scrape_pages_total{source,outcome}      counter
wcfp_scrape_bytes_total{domain}              counter
wcfp_tier_records_total{tier,outcome}        counter (outcome: ok|escalated|failed)
wcfp_tier_latency_seconds{tier}              histogram
wcfp_queue_depth{queue}                      gauge (per queue: main, dead, esc:tier2..4)
wcfp_dedup_decisions_total{outcome}          counter (outcome: same|different|uncertain)
wcfp_embedding_seconds                       histogram
wcfp_llm_parse_failures_total{model,tier}    counter
wcfp_pg_dump_bytes                           gauge
wcfp_age_sync_failures_total                 counter
wcfp_run_duration_seconds                    histogram (whole pipeline)
```

**Dashboard sections.** (1) Throughput: pages/min, records/min by tier.
(2) Quality: parse-fail %, dedup rate, escalation rate. (3) Cost: GPU minutes,
egress bytes. (4) Errors: dead-letter depth, failure rate.

**When.** MVP for metric exposition (Prometheus client lib in `pipeline.py`).
Grafana dashboard: Later.

---

## S9. Data versioning / audit trail (MVP)

**What.** Every write to `events`, `series`, `people`, `venues`, `orgs`
carries a `scrape_session_id`. A `pg.scrape_sessions` table tracks
`(session_id, started_at, finished_at, machine, git_sha, prompts_md_sha)`.

**Why.** "Why does the report say NeurIPS is in Lisbon?" — currently
unanswerable. With `scrape_session_id`, you can roll back: `DELETE FROM events
WHERE last_session_id = X` returns the corpus to the pre-bad-session state.
You also pin which `prompts.md` version produced which records — essential
when debugging prompt regressions.

**Implementation.** Add `last_session_id INT NOT NULL DEFAULT 0` to every
mutable table, foreign key to `scrape_sessions(session_id)`. Set a session-local
GUC at run start: `SET LOCAL wcfp.session_id = 42;` and have a default value
expression read from it. ~30 LOC change.

**When.** MVP. Do this before the corpus grows past ~1k records — adding it
later means backfilling.

---

## S10. Predatory conference blocklist (MVP)

**What.** A static list of known predatory publishers / spam conference
domains, checked **before** enqueue.

**Why.** LLM triage will let some predatory CFPs through. Beall's list and
its successors maintain ~1500 known-bad domains. A bloom-filter or set check
before enqueue is O(1) and cuts pollution.

**Implementation.** `wcfp/blocklist.py` with `is_predatory(domain) -> bool`.
Source: a curated list shipped in-repo as `data/blocklist.txt`. Refresh
quarterly from public sources (Cabells, retired Beall's snapshots). Combine
with the new `PROMPT_QUALITY_GUARD` (which catches things the static list
misses).

**When.** MVP. Cheap to add, prevents expensive cleanup later.

---

## S11. Idempotency keys on every queue push (MVP)

**What.** Every job pushed to Redis carries a stable `job_id = sha1(source_url
+ priority_bucket)`. Re-enqueueing the same URL is a no-op.

**Why.** Today, `wcfp:seen:{sha1(url)}` is the dedup mechanism. It's TTL'd at
30 days. If a worker crashes mid-job and the inflight lease expires, the job
is requeued — but the URL isn't in `seen` (because the original push was
de-duped). A second worker grabs it. Fine. But if the same URL gets enqueued
from two different categories simultaneously (e.g. a conference spans AI and
ML keyword searches), both pushes would happen. SETNX dedups them today —
that's already the right design. **No change needed**, but document this
clearly so it doesn't get refactored away.

**When.** MVP — as a comment in `queue.py` and a test case.

---

## S12. CORE rank canonical table (MVP)

**What.** Import the CORE conference and journal ranking portals once,
populate `pg.core_ranks(acronym, name, rank, source, year)`. At Tier 2/3
write time, override LLM-emitted `rank` with the canonical value if the
acronym matches.

**Why.** R11 — LLMs guess ranks based on prestige; CORE has the authoritative
list. Single import, periodic refresh (yearly).

**When.** MVP. Add `python -m wcfp import-core` CLI.

---

# Section 5 — Kubernetes Architecture (Future Target)

This section sketches the GKE deployment that preserves the pull→run→wipe
lifecycle in `context.md §18`. It is **not** the day-one architecture. It is
the design we should be able to migrate to without rewriting application
code — `WCFP_MACHINE` env var, the four-tier escalation queue, and the
GCS-as-canonical-store model all already align with K8s patterns.

## 5.1 Node pools

| Pool | Machine type | Min/Max nodes | GPU | Purpose |
|------|--------------|---------------|-----|---------|
| `cpu` | `e2-standard-4` (4 vCPU, 16 GB) | 0 / 2, on-demand | none | PG StatefulSet, Redis, scraper pods, runner orchestrator |
| `gpu-small` | `n1-standard-8` + 1× T4 (16 GB) | 0 / 1, spot | T4 | Tier 1/2 (qwen3:4b, qwen3:14b) |
| `gpu-large` | `n1-standard-16` + 1× L4 (24 GB) or A10 | 0 / 1, spot | L4/A10 | Tier 3 (qwen3:32b) + DeepSeek-R1:32b |
| `gpu-xlarge` | `a2-highgpu-1g` + 1× A100 (80 GB) | 0 / 1, spot | A100 | Tier 4 (deepseek-r1:70b) — only when queue exceeds threshold |

All GPU pools use **spot** preemptible instances. Preemption is expected; the
inflight lease + idempotent writes recover automatically.

## 5.2 Kubernetes resources

```
StatefulSet  postgres        cpu pool, 1 replica, 50 GB SSD PVC
Deployment   redis            cpu pool, 1 replica, AOF persistence (10 GB PVC)
StatefulSet  ollama-cpu       cpu pool, 1 replica, embed-text model only
StatefulSet  ollama-gpu-small gpu-small pool, 1 replica, models pre-baked in image
StatefulSet  ollama-gpu-large gpu-large pool, 1 replica, models pre-baked
Job          runner           cpu pool, 1 replica per pipeline run, terminates on completion
CronJob      weekly-run       wraps Job creation, schedule "0 2 * * 0" (Sun 02:00 UTC)
CronJob      tier4-batch      gpu-xlarge pool, schedule "0 3 1 * *" (monthly)
ScaledObject scraper-keda     KEDA-driven scraper Deployment, replicas based on queue depth
```

## 5.3 Why StatefulSet (not Deployment) for Ollama

Each Ollama replica owns a 50+ GB model cache on local SSD. StatefulSet
guarantees stable pod identity → stable PV binding → models aren't re-pulled
when the pod restarts. Deployment would lose the model cache on every
reschedule.

For pre-baked images (Q10 option 4), the StatefulSet still earns its keep
because the image itself is huge — local kubelet image cache prevents
re-pull only when the same node hosts the same pod, which StatefulSet
ensures.

## 5.4 Storage strategy

- **PostgreSQL PVC**: 50 GB pd-ssd, regional (replicated across two zones).
  Backed up to GCS via `pg_dump` cron sidecar (matches current model).
- **Redis PVC**: 10 GB pd-standard for AOF.
- **Ollama PVC**: optional. Recommendation: skip the PVC, embed models in
  image (Q10) — simpler.
- **GCS bucket**: durable store; all PVCs are recoverable from GCS.

## 5.5 Networking

ClusterIP services for `postgres:5432`, `redis:6379`, `ollama-gpu-large:11434`,
`ollama-gpu-small:11434`, `ollama-cpu:11434`. No ingress unless the health
endpoint (Suggestion 2) is exposed externally — recommend keeping it
internal-only behind a port-forward or VPN.

NetworkPolicy locks down: only `runner` and `scraper-*` pods can talk to PG;
only those plus the orchestrator can talk to Redis.

## 5.6 Workload Identity → GCS

```yaml
serviceAccountName: wcfp-runner
# bound to GCP IAM service account wcfp-runner@PROJECT.iam.gserviceaccount.com
# with role roles/storage.objectAdmin on the bucket
```

No JSON key file in any image. No env var leaks. `gsutil`/`rclone` inside the
pod uses the metadata server.

## 5.7 KEDA scaling for scraper workers

```yaml
ScaledObject scraper:
  scaleTargetRef: { name: scraper, kind: Deployment }
  minReplicaCount: 0
  maxReplicaCount: 20
  triggers:
    - type: redis
      metadata:
        address: redis:6379
        listName: wcfp:queue
        listLength: "10"  # 1 worker per 10 queued items
```

Scrapers join an existing run; the Job pod is the orchestrator that decides
when to terminate (queue empty AND inflight=0 for 5 min).

## 5.8 Cost model — weekly pipeline run

Assumptions: 500 URLs scraped (steady state, after first run), 80% Tier 1,
15% Tier 2, 4% Tier 3, 1% Tier 4 deferred to monthly batch. Weekly run on
Sundays 02:00 UTC.

| Component | Hours/week | Rate (us-central1, spot) | Weekly | Monthly |
|-----------|------------|--------------------------|--------|---------|
| `cpu` pool: 1× e2-standard-4 | 2 h | $0.05 / h on-demand | $0.10 | $0.43 |
| `gpu-small` pool: 1× T4 | 1 h | ~$0.11 / h spot | $0.11 | $0.47 |
| `gpu-large` pool: 1× L4 | 0.5 h | ~$0.30 / h spot | $0.15 | $0.65 |
| `gpu-xlarge` pool: 1× A100 (monthly Tier 4) | 1 h / month | ~$1.30 / h spot | — | $1.30 |
| PG PVC 50 GB pd-ssd | 24×7 | $0.17 / GB-month | — | $8.50 |
| Redis PVC 10 GB pd-standard | 24×7 | $0.04 / GB-month | — | $0.40 |
| GCS bucket: 10 GB stored, ~2 GB egress / month | — | $0.02/GB-month + $0.12/GB egress | — | $0.44 |
| Egress to internet (scraping has zero ingress cost) | — | — | — | $0 |
| GKE management fee (cluster) | — | $0.10 / h | — | $73 |
| **Total estimated** | | | | **~$85/month** |

Notes:
- The GKE management fee dominates. **Use GKE Autopilot in `pricing-mode=spot`
  if available** to remove the per-cluster fee for low-utilisation clusters,
  or run the cluster only during pipeline runs (kubectl-driven scale-up of
  the control plane is not possible — control plane is always on for GKE
  Standard). For ~weekly use, **GKE Autopilot is more cost-effective** at
  this scale: ~$0.10/h while running, $0 idle.
- Cost goes up with first-run scrape (5680 URLs ≈ 12× weekly volume): roughly
  one extra $30 spike that month.
- A spot preemption recovery costs ~$1 (one extra hour of GPU). Budget for
  20% preemption rate.

For comparison: laptop run is $0/month direct (sunk cost in hardware).

## 5.9 Migration trigger conditions

Stay on Docker Compose unless one of:
1. The user wants automated weekly runs without leaving a laptop on.
2. Tier 4 batch backlog exceeds 500 items and the user lacks a DGX.
3. Multi-source parallel scraping is required (scaling beyond WikiCFP +
   ai-deadlines).
4. A second user joins the project and concurrent sessions become necessary
   (in which case revisit ADR-8).

---

# Architectural questions surfaced beyond the user's list

The user-supplied list of 10 questions covered most of the surface. Three
additional questions emerged during this review:

- **Q11. Redis durability of `wcfp:cursor:{source}` and `wcfp:dead`** — these
  are operational by classification but business-critical in practice. Loss
  of cursor → re-scrape from page 1; loss of dead-letter → silent failure.
  Recommendation: AOF + selective PG mirroring (Section 1, Q11).
- **Q12. JSON-mode failure recovery** — every prompt demands JSON; quantised
  models fail occasionally. The spec doesn't specify retry semantics.
  Recommendation: local repair → one same-tier retry → escalate.
- **Q13. Cypher query cost ceiling** — unbounded `*0..` traversals will
  eventually be slow. Cap depth, materialise transitive closures when the
  graph grows.
- **Q14. Quantisation policy per profile** — Ollama's defaults can silently
  pick Q4 on small VRAM, costing JSON validity. Pin per profile.
- **Q15. Workshop entity modelling** — `Workshop` graph node vs `is_workshop`
  flag is ambiguous. Pick one (recommend the flag).

These are folded into Section 1 with full analysis.
