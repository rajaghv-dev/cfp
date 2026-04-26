---
name: Architecture decisions — cfp project
description: All settled technical decisions. Do not re-propose alternatives.
type: project
---

## Deployment Model (2026-04-26 — supersedes multi-machine design)
- **Single machine operation** — any machine with Docker + Ollama runs the pipeline
- Machine lifecycle: **pull from GCS → restore → run → sync to GCS → full local wipe**
- All persistent data lives in GCS (pg_dump + Parquet). Local state is always ephemeral.
- `WCFP_MACHINE` profile: `dgx | gpu_large | gpu_mid | gpu_small | cpu_only`
- Single local Ollama daemon at `OLLAMA_HOST=http://localhost:11434` (no per-machine routing)
- `PROFILE_MODELS` dict in config.py controls which models are pulled per profile

## Database Stack
- **PostgreSQL 16 + pgvector** = source of truth for v1
  - v1 Docker image: `pgvector/pgvector:pg16` (standard, no AGE complexity)
  - v2 Docker image: `apache/age:PG16_latest` (adds AGE knowledge graph)
- **Apache AGE** = knowledge graph + live ontology — v2 only
- **DuckDB** = analytics ONLY — reads PG via `postgres_scanner`, never writes — v2 only (v1 uses direct PG queries)
- **Redis** = queue + rate limiting ONLY — zero business data, no backup needed
- Rejected: Qdrant/LanceDB (no JOIN with relational), Neo4j (no vectors/SQL), MongoDB

## v1 vs v2 Scope Split (arch.md §6)
- **v1**: Tiers 1+2, pgvector, pgvector-only dedup, WikiCFP + ai-deadlines, direct PG queries for reports
- **v2**: AGE graph, Tier 3+4, DeepSeek-R1 dedup confirmation, DuckDB analytics, ontology pipeline
- Migration is additive — no rewrites between v1 and v2

## LLM Pipeline
- **Qwen3** = ALL tool calling (only reliable Ollama family with tool support, April 2025)
- **DeepSeek-R1** = pure reasoning only (dedup, Tier 4). No tool calling.
- **mistral-nemo:12b** = long-context HTML when >32k tokens
- **nomic-embed-text** = 768-d embeddings → pgvector (300 MB, CPU fallback)
- 4-tier cascade: qwen3:4b → qwen3:14b → qwen3:32b → deepseek-r1:70b (confidence-gated)
- v1 ships with Tiers 1+2 only; Tier 3+4 are v2

## Data Model — Canonical Field Names
- Paper submission deadline field: **`paper_deadline`** everywhere (model, DB, prompts, parsers)
  - Old name `deadline` is deprecated — must be renamed at implementation time in specs 04 and 11
- Workshop flag: **`is_workshop: bool`** on the Event dataclass (NOT a standalone Workshop graph node)
  - Workshop graph node label is dropped from v1; added back in v2 if needed

## Knowledge Graph (v2)
- Graph name: `wcfp_graph`
- AGE graph IS the live ontology; `.owl` file is read-only export for Protégé
- Node labels: Conference, ConferenceSeries, Person, Organisation, Venue, City, Country, Concept, RawTag
- Workshop node label dropped — replaced by `is_workshop` flag on Conference node
- Full edge type list in `context.md §5`

## Ontology
- Built bottom-up from scraped raw_tags — not top-down from a fixed taxonomy
- Pipeline: Tier1 extracts → Tier2 clusters synonyms (pgvector) → Tier3 infers IS_A → Tier4 validates
- Prompts: `PROMPT_ONTOLOGY_SYNONYM` + `PROMPT_ONTOLOGY_ISA` + TIER4 ontology edges — all in prompts.md
- Bootstrap: hand-authored `ontology/seed_concepts.json` (13 Category values + ~50 subconcepts)
- owlready2/rdflib = export-only layer → `.owl` for Protégé — v2 only

## Persistence and Sync
- GCS (rclone) for off-machine persistence: pg_dump + Parquet archives + reports
- Reports + `data/latest.json` → git push (lightweight, always tracked)
- Heavy data (pg_dump, embeddings, Parquet) → GCS only, never git

## Non-Negotiable Constraints
1. `models.py` + `config.py` import NOTHING project-internal
2. All writes → psycopg3 to PostgreSQL (not DuckDB, not psycopg2)
3. DuckDB never writes to disk in this project
4. Redis stores zero business data
5. Tool calling: Qwen3 ONLY
6. WikiCFP paired-row parser in `scraper.py` is correct — copy verbatim to `wcfp/parsers/wikicfp.py`
7. India location taxonomy in `generate_md.py` is correct — copy verbatim
8. COALESCE upsert: never overwrite `notification`, `camera_ready`, `rank`, `notes`, `official_url`, `submission_system` with NULL
9. Crawl: min 5s delay, Gaussian(8s, σ=2.5), 10% chance 15–45s long pause
10. `paper_deadline` is the canonical name — not `deadline`

## Scalability Decisions (arch.md §4 S13–S15)
- `fetch.py` must use `aiohttp` (async), not `requests` — 3–5× throughput gain
- Embeddings batched 32–64 per Ollama call — 10–20× gain on embed generation
- Redis queue supports `--workers N` with zero code changes to queue.py
