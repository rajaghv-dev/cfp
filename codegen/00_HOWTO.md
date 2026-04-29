# How to Use the Codegen Files

## Purpose
Each file in this directory is a self-contained specification for Claude Sonnet to implement
one or more source files. They are ordered by dependency: implement in numeric order.

## How to Use in a New Session

1. Read `SESSION.md` first (2 min context)
2. Pick the next unimplemented file number
3. Paste the content of `codegen/NN_name.md` to Claude Sonnet as your prompt
4. Sonnet will generate the implementation; review and commit
5. Move to the next number

## Dependency Order

```
01 config_models     → no project deps (implement first)
02 prompts_parser    → needs models.py
03 fetch_http        → needs models.py, config.py
04 wikicfp_parser    → needs models.py, fetch.py
05 db_schema         → needs models.py, config.py
06 graph_age         → needs models.py, config.py, db.py
07 queue_redis       → needs models.py, config.py
08 vectors_embed     → needs models.py, config.py, db.py
09 llm_client        → needs models.py, config.py
10 tier_llm          → needs llm/client.py, models.py, prompts (from prompts.md)
11 analytics_generate → needs config.py, models.py, analytics conn
12 pipeline_cli      → needs all of the above
13 setup_infra       → docker-compose, Makefile, requirements.txt, setup.sh
14 agents_patterns   → AGENTS.md, PATTERNS.md (human + agent docs)
```

## Key Constraints for All Generated Code

- `cfp/models.py` and `config.py` MUST import nothing project-internal
- All writes to PostgreSQL use `psycopg` (psycopg3), NOT psycopg2
- DuckDB is NEVER used for writes; it only reads PostgreSQL via postgres_scanner
- Redis stores ZERO business data
- Tool calling: Qwen3 models ONLY. DeepSeek-R1 has no tool calling
- Import style: `from cfp.models import Event, Category` (absolute imports)
- All dates: `datetime.date`. All timestamps: `datetime.datetime` UTC-aware
- All dataclasses use `@dataclass(slots=True)`

## Source Files to Reference

- `context.md` — full architecture (19 sections)
- `prompts.md` — all LLM prompts + search queries
- `SESSION.md` — current state and what exists

## What Already Works (Do Not Break)

- `generate_md.py` location taxonomy (`_UK`, `_CH`, `_EU_EXCL`, `_USA`, `_SG`,
  `_INDIA_RE`, `CITY_STATE`, `location_tags()`, `india_state()`) — COPY VERBATIM
- `scraper.py` WikiCFP paired-row parser (`find_data_table()`, `parse_table()`) — COPY VERBATIM
- `data/latest.json` — 350 conferences, use as seed data for first `init-db` run
