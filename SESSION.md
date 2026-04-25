# Session State — CFP Conference Knowledge Pipeline

> Read this at the start of every new session for full context in ~2 minutes.
> Last updated: 2026-04-25

---

## Repo

| | |
|---|---|
| **Local path** | `/home/raja/wiki-cfp` |
| **GitHub** | https://github.com/rajaghv-dev/cfp |
| **Branch** | `main` |
| **Clone command** | `bash setup.sh https://github.com/rajaghv-dev/cfp.git` |

---

## What This Is

Conference knowledge pipeline: scrapes WikiCFP + ai-deadlines + more sources,
classifies via 4-tier LLM pipeline (Qwen3 4b→14b→32b + DeepSeek-R1 70b),
stores in PostgreSQL + pgvector + Apache AGE knowledge graph, generates Markdown reports.

People, venues, organisations, ontology concepts are all first-class graph nodes.

---

## Architecture (final — do not re-discuss)

| Component | Role |
|---|---|
| PostgreSQL 16 + pgvector + Apache AGE | Source of truth (structured + vectors + graph) |
| DuckDB | Analytics ONLY — reads PG via postgres_scanner, never writes |
| Redis | Queue + rate limiting ONLY — zero business data |
| Qwen3 (4b/14b/32b) | Tier 1–3: classification + tool calling. Ollama only. |
| DeepSeek-R1 (32b/70b) | Dedup reasoning + Tier 4 batch. No tool calling. |
| nomic-embed-text | 768-d embeddings → pgvector |

Full spec: `context.md` · All LLM prompts: `prompts.md`

---

## Hardware

| Machine | GPU | VRAM | Models (WCFP_MACHINE=) |
|---|---|---|---|
| Workstation A | RTX 4090 | 24 GB | `rtx4090` → qwen3:32b, deepseek-r1:32b |
| Workstation B | RTX 3080 | 16 GB | `rtx3080` → qwen3:4b, qwen3:14b, mistral-nemo:12b, nomic-embed-text |
| DGX Station | 8× A100 | 256 GB | `dgx` → deepseek-r1:70b |

---

## Current File State

### Working (do not break)
| File | Status | Notes |
|---|---|---|
| `scraper.py` | ✅ Working | WikiCFP BS4 scraper — paired-row parser is correct |
| `generate_md.py` | ✅ Working | India state-wise reports — location taxonomy is correct |
| `prompts.md` | ✅ Complete | All search queries + 7 LLM prompt bodies |
| `context.md` | ✅ Complete | 19-section architecture spec |
| `data/latest.json` | ✅ Seed data | 350 conferences — used to seed PostgreSQL |
| `reports/*.md` | ✅ Generated | 13 Markdown reports |
| `setup.sh` | ✅ Updated | Clone + venv + pip + optional Ollama pull |

### Partially created (codegen specs only — no implementation yet)
| File | Status |
|---|---|
| `codegen/00_HOWTO.md` | ✅ Created |
| `codegen/01_config_models.md` | ✅ Created — spec for config.py + wcfp/models.py |
| `codegen/04_wikicfp_parser.md` | ✅ Created — spec for wcfp/parsers/ |
| `codegen/05_db_schema.md` | ✅ Created — spec for wcfp/db.py |
| `codegen/09_llm_client.md` | ✅ Created — spec for wcfp/llm/client.py + tools.py |
| `codegen/11_analytics_generate.md` | ✅ Created — spec for analytics.py + generate_md.py |
| `codegen/02,03,06,07,08,10,12,13,14` | 🔲 Not yet created |

### Not yet created (wcfp/ package)
Everything in `wcfp/` is still missing. All files described in `codegen/` specs.

---

## Next Session: Implement the wcfp/ Package

### Step 1 — Create foundation files
Use `codegen/01_config_models.md` → create `config.py` and `wcfp/models.py`

### Step 2 — Create remaining codegen specs (02, 03, 06, 07, 08, 10, 12, 13, 14)
These were not created yet. Create them before implementing (see Plan agent output in conversation).

### Step 3 — Implement in dependency order
```
config.py + wcfp/models.py          ← codegen/01
wcfp/prompts_parser.py              ← codegen/02
wcfp/fetch.py                       ← codegen/03
wcfp/parsers/                       ← codegen/04
wcfp/db.py                          ← codegen/05
wcfp/graph.py                       ← codegen/06
wcfp/queue.py                       ← codegen/07
wcfp/vectors.py + wcfp/embed.py     ← codegen/08
wcfp/llm/                           ← codegen/09 + 10
wcfp/analytics.py + generate_md.py ← codegen/11
wcfp/pipeline.py + wcfp/cli.py      ← codegen/12
setup.sh + docker-compose + Makefile ← codegen/13
AGENTS.md + PATTERNS.md             ← codegen/14
```

### Step 4 — Delete scraper.py (after wcfp/parsers/wikicfp.py is implemented)

---

## Key Constraints (never violate)

1. `models.py` and `config.py` import NOTHING project-internal
2. All writes → PostgreSQL via psycopg3 (NOT psycopg2, NOT DuckDB)
3. DuckDB is read-only analytics — never writes to disk
4. Redis stores zero business data
5. Tool calling: Qwen3 ONLY. DeepSeek-R1 = pure reasoning, no tools
6. WikiCFP paired-row parsing in `scraper.py` is CORRECT — copy verbatim to `wcfp/parsers/wikicfp.py`
7. India state-wise location taxonomy in `generate_md.py` is CORRECT — copy verbatim
8. COALESCE upsert: never overwrite notification, camera_ready, rank, notes with NULL
9. Crawl delays: min 5s, Gaussian(8, 2.5), 10% chance 15–45s pause

---

## Merged From: conf-scr-org-syn

Repo analyzed: https://github.com/rajaghv-dev/conf-scr-org-syn  
Clone at: /tmp/conf-scr-org-syn (may need to re-clone)

Key patterns to incorporate — all captured in codegen specs:
- `_is_english()` filter → `wcfp/parsers/wikicfp.py`
- `_safe_parse_date()` dateutil fuzzy → `wcfp/parsers/wikicfp.py`
- Abstract+paper deadline regex → `wcfp/parsers/wikicfp.py`
- COALESCE upsert → `wcfp/db.py`
- `_parse_json_response()` 3-level fallback → `wcfp/llm/client.py`
- `_strip_thinking()` → `wcfp/llm/client.py`
- `slug` + `days_to_deadline` properties → `wcfp/models.py`
- `to_markdown()` → `wcfp/models.py`
- `## Notes` preservation → `generate_md.py`
- `scrape_ai_deadlines()` YAML → `wcfp/parsers/ai_deadlines.py`
- Rich CLI deadline coloring → `wcfp/cli.py`
- PATTERNS.md deadline statistics → `PATTERNS.md`

---

## Quick Reference

| Need | Where |
|---|---|
| Architecture | `context.md` (19 sections) |
| LLM prompts | `prompts.md` (PROMPT_TIER1..4, PROMPT_DEDUP, etc.) |
| How to implement | `codegen/00_HOWTO.md` |
| Data models spec | `codegen/01_config_models.md` |
| PostgreSQL schema | `codegen/05_db_schema.md` |
| Graph schema | `context.md §5` |
| Redis keys | `context.md §8` |
| Clone + setup | `bash setup.sh https://github.com/rajaghv-dev/cfp.git` |
