# Session State — CFP Conference Knowledge Pipeline

> Read this at the start of every new session for full context in ~2 minutes.
> Last updated: 2026-04-26

---

## Repo

| | |
|---|---|
| **Local path** | `/home/raja/cfp` |
| **GitHub** | https://github.com/rajaghv-dev/cfp |
| **Branch** | `main` |
| **Clone command** | `bash setup.sh https://github.com/rajaghv-dev/cfp.git` |

---

## What This Is

Conference knowledge pipeline: scrapes WikiCFP + ai-deadlines + email (Gmail API) + more sources,
classifies via 4-tier LLM pipeline (Qwen3 4b→14b→32b + DeepSeek-R1 32b/70b),
stores in PostgreSQL 16 + pgvector + Apache AGE knowledge graph, generates Markdown reports.

People, venues, organisations, ontology concepts are all first-class graph nodes.
All persistent data lives in GCS. Local state is always ephemeral.

---

## Architecture

| Component | Role |
|---|---|
| PostgreSQL 16 + pgvector + Apache AGE | Source of truth (structured + vectors + graph) |
| DuckDB | Analytics ONLY — reads PG via postgres_scanner, never writes |
| Redis | Queue + rate limiting ONLY — zero business data |
| Qwen3 (4b/14b/32b) | Tier 1–3: classification + tool calling. Ollama only. |
| DeepSeek-R1 (32b/70b) | Dedup reasoning + Tier 4 batch. No tool calling. |
| nomic-embed-text | 768-d embeddings → pgvector |
| GCS (rclone) | Off-machine persistence: pg_dump + Parquet + reports |
| Single machine | Any machine with Docker + Ollama. WCFP_MACHINE profile controls tiers. |

Machine lifecycle: **pull from GCS → restore → run → sync to GCS → full local wipe.**

Full spec: `context.md` · Prompts: `prompts.md` · Deep arch: `arch.md` · Learning: `lesson_plan.md`

---

## Hardware Profiles

| `WCFP_MACHINE` | Min VRAM | Tiers / Models |
|---|---|---|
| `dgx` | 80 GB | All tiers + deepseek-r1:70b for Tier 4 |
| `gpu_large` | 24 GB | Tiers 1–4 (qwen3:4b + qwen3:14b + qwen3:32b + deepseek-r1:32b) |
| `gpu_mid` | 10 GB | Tiers 1–2 (qwen3:4b + qwen3:14b) |
| `gpu_small` | 4 GB | Tier 1 only (qwen3:4b) |
| `cpu_only` | — | Tier 1 only (qwen3:4b, slow) |

`nomic-embed-text` runs on all profiles (300 MB VRAM / CPU fallback).

---

## Current File State

### Complete and working (do not break)
| File | Status | Notes |
|---|---|---|
| `scraper.py` | ✅ Working | WikiCFP BS4 scraper — paired-row parser is correct |
| `generate_md.py` | ✅ Working | India state-wise reports — location taxonomy is correct |
| `data/latest.json` | ✅ Seed data | 350 conferences — used to seed PostgreSQL on first run |
| `reports/*.md` | ✅ Generated | 13 Markdown reports |

### Documentation (complete)
| File | Lines | Notes |
|---|---|---|
| `CLAUDE.md` | 78 | Standing session instructions — auto-loaded by Claude Code |
| `context.md` | 679 | 20-section architecture spec (§19 open questions, §20 risks) |
| `arch.md` | 1,210 | 15 open questions · 18 risks · 8 ADRs · 12 suggestions · K8s spec |
| `prompts.md` | 1,008 | 12 LLM prompts + search queries + parsers + external sources |
| `lesson_plan.md` | 912 | 14-module learning curriculum + A–Z glossary |
| `SESSION.md` | this | Current state |
| `setup.sh` | — | Clone + venv + pip + optional Ollama pull |

### Codegen specs (written — no implementation yet)
| File | Covers | Last reviewed |
|---|---|---|
| `codegen/00_HOWTO.md` | How to use the codegen files | — |
| `codegen/01_config_models.md` | `config.py` + `wcfp/models.py` | 2026-04-26 (gap audit: OLLAMA_HOST single, paper_deadline, is_workshop, sponsor_names, scrape_session_id, PersonRole.ORGANIZER/OTHER, OrgType.OTHER) |
| `codegen/04_wikicfp_parser.md` | `wcfp/parsers/` | — (note: still emits `Event(deadline=…)` — must rename to `paper_deadline` at implementation time) |
| `codegen/05_db_schema.md` | `wcfp/db.py` | 2026-04-26 (gap audit: paper_deadline, is_workshop, submission_system, sponsor_names, quality_flags, quality_severity, scrape_sessions table + FK, sites.last_cursor, COALESCE policy split) |
| `codegen/09_llm_client.md` | `wcfp/llm/client.py` + `tools.py` | 2026-04-26 (gap audit: OLLAMA_HOST/PROFILE_MODELS imports, single-host OllamaClient, get_available_models, profile_intersection) |
| `codegen/11_analytics_generate.md` | `wcfp/analytics.py` + `generate_md.py` | — (note: SELECT still references `deadline::VARCHAR` — must update to `paper_deadline::VARCHAR`) |

### Codegen specs — NOT YET WRITTEN
| Spec | Module |
|---|---|
| `codegen/02` | `wcfp/prompts_parser.py` |
| `codegen/03` | `wcfp/fetch.py` |
| `codegen/06` | `wcfp/graph.py` (Apache AGE) |
| `codegen/07` | `wcfp/queue.py` (Redis) |
| `codegen/08` | `wcfp/vectors.py` + `wcfp/embed.py` |
| `codegen/10` | `wcfp/llm/tier1..4.py` |
| `codegen/12` | `wcfp/pipeline.py` + `wcfp/cli.py` |
| `codegen/13` | `setup.sh` + `docker-compose.yml` + `Makefile` |
| `codegen/14` | `AGENTS.md` + `PATTERNS.md` |
| `codegen/15` | `wcfp/dedup.py` |
| `codegen/16` | `wcfp/sync.py` |
| `codegen/17` | `wcfp/ontology.py` |

### Implementation — NOT YET STARTED
`wcfp/` package does not exist. All modules are unimplemented.

---

## Blocking Arch Questions (must resolve before coding these modules)

| Question | Blocks | Status |
|---|---|---|
| Q4 — AGE consistency (derived tables vs authoritative) | `wcfp/graph.py` | open (v2) |
| Q6 — Cross-source dedup trigger timing | `wcfp/dedup.py` | open (v2 needs LLM confirmation; v1 uses pgvector-only) |
| Q10 — Ollama model storage (named volume vs re-pull) | `docker-compose.yml`, `setup.sh` | open |
| Q12 — JSON-mode failure retry budget | `wcfp/llm/client.py` | open |
| Q14 — Quantisation policy per WCFP_MACHINE profile | `config.py` | open |
| Q15 — Workshop: `is_workshop` flag vs `Workshop` graph node | `wcfp/models.py`, `wcfp/graph.py` | **PARTIALLY RESOLVED** — `is_workshop=True` flag is now on `Event`; standalone `Workshop` graph node will be dropped from `context.md §5` when v2 graph spec is finalised |

Full question details + recommended answers: `arch.md §1`

---

## Next Steps (in order)

**Build v1 first.** v1 scope is defined in `arch.md §6` — Tiers 1+2 only,
PostgreSQL+pgvector only (no AGE), pgvector-cosine dedup only (no DeepSeek-R1
confirmation), direct PG queries (no DuckDB), WikiCFP + ai-deadlines only.
v2 components (AGE, Tier 3+4, ontology, DuckDB) are an additive migration
post-v1.

1. **Resolve remaining blocking arch questions** (Q10, Q12, Q14 hit v1).
   Q4 / Q6 / Q15 belong to v2 modules — defer.
2. **Write missing codegen specs for v1 modules** (02, 03, 07, 08, 13, 15, 16).
   v2-only specs (06 graph, 10 tier3/4, 17 ontology) deferred.
3. **Implement v1 in dependency order** (see below).
4. **Delete `scraper.py`** after `wcfp/parsers/wikicfp.py` is verified working.
5. **Run v1 weekly for a month** with real data before designing v2.

### Implementation order
```
config.py + wcfp/models.py          ← spec 01  ← start here
wcfp/prompts_parser.py              ← spec 02
wcfp/fetch.py                       ← spec 03
wcfp/parsers/ (wikicfp + ai_deadlines) ← spec 04
wcfp/db.py                          ← spec 05
wcfp/graph.py                       ← spec 06  (needs Q4 resolved)
wcfp/queue.py                       ← spec 07
wcfp/vectors.py + wcfp/embed.py     ← spec 08
wcfp/llm/client.py + tools.py       ← spec 09
wcfp/llm/tier1..4.py               ← spec 10
wcfp/analytics.py + generate_md.py ← spec 11
wcfp/dedup.py                       ← spec 15  (needs Q6 resolved)
wcfp/sync.py                        ← spec 16
wcfp/ontology.py                    ← spec 17
wcfp/pipeline.py + wcfp/cli.py     ← spec 12
setup.sh + docker-compose + Makefile ← spec 13
AGENTS.md + PATTERNS.md             ← spec 14
```

### Enhancements (after v1 ships)
- Gmail integration (`wcfp/parsers/email_gmail.py`)
- EDAS / EasyChair / OpenReview parsers
- Kubernetes migration (spec in `arch.md §5`)
- Health check endpoint (FastAPI)
- Predatory publisher blocklist
- Prometheus + Grafana observability
- lesson_plan.md Modules 14–21 (async, BS4, HTTP, testing, Docker, git, scraping ethics)

---

## Key Constraints (never violate)

1. `models.py` and `config.py` import NOTHING project-internal
2. All writes → PostgreSQL via psycopg3 (NOT psycopg2, NOT DuckDB)
3. DuckDB is read-only analytics — never writes to disk
4. Redis stores zero business data
5. Tool calling: Qwen3 ONLY. DeepSeek-R1 = pure reasoning, no tools
6. WikiCFP paired-row parsing in `scraper.py` is CORRECT — copy verbatim to `wcfp/parsers/wikicfp.py`
7. India state-wise location taxonomy in `generate_md.py` is CORRECT — copy verbatim
8. COALESCE upsert: never overwrite notification, camera_ready, rank, notes, submission_system, sponsor_names, official_url, description with NULL. **Always overwrite** (direct update): paper_deadline, abstract_deadline, dates, location, quality_flags, quality_severity, scrape_session_id
9. Crawl delays: min 5s, Gaussian(8, 2.5), 10% chance 15–45s pause
10. **Canonical field name is `paper_deadline`** — not `deadline`. Used uniformly across `models.Event`, `events.paper_deadline` column, prompts.md, parsers, Markdown output. The legacy name `deadline` must not appear in new code.
11. **Single Ollama daemon per machine** at `OLLAMA_HOST` — no per-model host routing. Tier escalation handles model availability via `PROFILE_MODELS[WCFP_MACHINE]`.

---

## Patterns to Carry Forward (from conf-scr-org-syn repo)

| Pattern | Destination |
|---|---|
| `_is_english()` filter | `wcfp/parsers/wikicfp.py` |
| `_safe_parse_date()` dateutil fuzzy | `wcfp/parsers/wikicfp.py` |
| Abstract + paper deadline regex | `wcfp/parsers/wikicfp.py` |
| COALESCE upsert | `wcfp/db.py` |
| `_parse_json_response()` 3-level fallback | `wcfp/llm/client.py` |
| `_strip_thinking()` | `wcfp/llm/client.py` |
| `slug` + `days_to_deadline` properties | `wcfp/models.py` |
| `to_markdown()` | `wcfp/models.py` |
| `## Notes` preservation | `generate_md.py` |
| `scrape_ai_deadlines()` YAML | `wcfp/parsers/ai_deadlines.py` |
| Rich CLI deadline coloring | `wcfp/cli.py` |
| PATTERNS.md deadline statistics | `PATTERNS.md` |

---

## Quick Reference

| Need | Where |
|---|---|
| Standing session instructions | `CLAUDE.md` |
| Architecture spec | `context.md` (20 sections) |
| Deep arch analysis + open questions | `arch.md` |
| All LLM prompts (12 total) | `prompts.md` |
| Learning guide | `lesson_plan.md` |
| How to implement | `codegen/00_HOWTO.md` |
| Data models spec | `codegen/01_config_models.md` |
| PostgreSQL schema | `codegen/05_db_schema.md` |
| Graph schema | `context.md §5` |
| Redis keys | `context.md §8` |
| Machine lifecycle | `context.md §18` |
| Kubernetes spec | `arch.md §5` |
| Clone + setup | `bash setup.sh https://github.com/rajaghv-dev/cfp.git` |
