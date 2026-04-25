---
name: Architecture decisions — cfp project
description: All settled technical decisions. Do not re-propose alternatives.
type: project
---

## Database Stack (final)
- **PostgreSQL 16 + pgvector + Apache AGE** = source of truth
  - Vector + graph + relational in one SQL+Cypher query — unique capability
  - Docker: `apache/age:PG16_latest` (PG16 + AGE pre-installed)
- **DuckDB** = analytics ONLY — reads PG via `postgres_scanner`, never writes to disk
- **Redis** = queue + rate limiting ONLY — zero business data stored
- Rejected: Qdrant/LanceDB (no JOIN with relational), Neo4j (no vectors/SQL), MongoDB

## Knowledge Graph (Apache AGE)
Graph name: `wcfp_graph`. Node labels: Conference, ConferenceSeries, Person, Organisation, Venue, City, Country, Concept, RawTag, Workshop.
Full edge type list in `context.md §5`.

## People / Venues / Orgs
First-class entities in both the relational schema (PostgreSQL tables) and the AGE graph.
Enables: PC chair network queries, venue history, org sponsorship graphs, India state-wise with institution affiliations.

## LLM Pipeline
- **Qwen3** = ALL tool calling. Only local family with reliable Ollama tool-call support (April 2025).
- **DeepSeek-R1** = pure reasoning only (dedup, Tier 4). No tool calling.
- **mistral-nemo:12b** = long-context HTML extraction when >32k tokens (128k ctx window).
- **nomic-embed-text** = 768-d embeddings → pgvector (274 MB, CPU-friendly).

## 4-Tier Pipeline
```
Tier 1: qwen3:4b   RTX3080  conf>=0.85→save  else→escalate  (~80% stop here)
Tier 2: qwen3:14b  RTX3080  conf>=0.85→save  else→escalate  (~15%)
Tier 3: qwen3:32b  RTX4090  conf>=0.80→save  else→escalate  (~4%)
Tier 4: deepseek-r1:70b DGX  always final (overnight batch)  (~1%)
```

## Ontology
AGE graph IS the live ontology (Cypher-queryable, always current).
`owlready2` + `rdflib` = export-only layer → `.owl` file for Protégé inspection.
Not the primary store.

## State Sync
`rclone` + Backblaze B2: `pg_dump` + Parquet archives.
Reports + `data/latest.json` → git push.
Heavy data never goes to git.

## Machine Routing
`WCFP_MACHINE=rtx3080|rtx4090|dgx|local` env var controls which Ollama models `setup.sh` pulls.

## Non-Negotiable Constraints
1. `models.py` + `config.py` import NOTHING project-internal
2. All writes → psycopg3 to PostgreSQL (not DuckDB, not SQLite)
3. DuckDB never writes to disk in this project
4. Redis stores zero business data
5. Tool calling: Qwen3 ONLY
6. WikiCFP paired-row parser (`scraper.py` `parse_table()`) is correct — copy verbatim
7. India location taxonomy (`generate_md.py`) is correct — copy verbatim
8. COALESCE upsert: never overwrite `notification`, `camera_ready`, `rank`, `notes` with NULL
9. Crawl: min 5s delay, Gaussian(8s, σ=2.5), 10% chance 15–45s long pause
