# Session State — CFP Conference Knowledge Pipeline

> Read this at the start of every new session for full context in ~2 minutes.
> Last updated: 2026-04-29

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

| Component | v1 | v2 | Role |
|---|---|---|---|
| PostgreSQL 16 + pgvector | ✅ | ✅ | Source of truth — structured + vectors |
| Apache AGE (graph) | — | ✅ | Knowledge graph + live ontology |
| DuckDB | — | ✅ | Analytics ONLY — reads PG via postgres_scanner |
| Redis | ✅ | ✅ | Queue + rate limiting ONLY — zero business data |
| Qwen3 4b + 14b | ✅ | ✅ | Tiers 1–2: triage + extraction |
| Qwen3 32b + DeepSeek-R1 | — | ✅ | Tiers 3–4: tool-calling + batch reasoning |
| nomic-embed-text | ✅ | ✅ | 768-d embeddings → pgvector |
| GCS (rclone) | ✅ | ✅ | Off-machine persistence: pg_dump + reports |
| Single machine | ✅ | ✅ | Any machine with Docker + Ollama. CFP_MACHINE profile controls tiers. |

Machine lifecycle: **pull from GCS → restore → run → sync to GCS → full local wipe.**

Full spec: `context.md` · Prompts: `prompts.md` · Deep arch: `arch.md` · Learning: `lesson_plan.md`

---

## Hardware Profiles

| `CFP_MACHINE` | Min VRAM | Tiers / Models |
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
| `CLAUDE.md` | 80 | Standing session instructions — auto-loaded by Claude Code |
| `context.md` | 685 | 20-section architecture spec — v1/v2 annotations, Q10/Q12/Q14/Q15 resolved |
| `arch.md` | 1,485 | 15 questions (Q10/Q12/Q14/Q15 RESOLVED) · 18 risks · 8 ADRs · 15 suggestions |
| `prompts.md` | 1,008 | 12 LLM prompts + search queries + parsers + external sources |
| `lesson_plan.md` | 1,659 | 35-module learning curriculum + expanded A–Z glossary |
| `evals.md` | 255 | Model research log — what runs on 16 GB VRAM, eval-backed recommendations |
| `requirements.txt` | 40 | Full v1 deps grouped by purpose; v2-only deps commented out |
| `README.md` | 181 | Current project README with architecture + quick start |
| `.env.example` | 35 | All env vars with defaults and comments |
| `docker-compose.yml` | — | postgres+pgvector, redis+AOF, ollama+GPU+bind-mount |
| `scripts/setup_postgres.sh` | — | Native PG16+pgvector install for WSL2 (fallback to Docker) |
| `SESSION.md` | this | Current state + priority to-do list |
| `setup.sh` | — | Clone + venv + pip + optional Ollama pull |

### Codegen specs (written — no implementation yet)
| File | Covers | Last reviewed |
|---|---|---|
| `codegen/00_HOWTO.md` | How to use the codegen files | — |
| `codegen/01_config_models.md` | `config.py` + `cfp/models.py` | 2026-04-26 (gap audit: OLLAMA_HOST single, paper_deadline, is_workshop, sponsor_names, scrape_session_id, PersonRole.ORGANIZER/OTHER, OrgType.OTHER) |
| `codegen/04_wikicfp_parser.md` | `cfp/parsers/` | 2026-04-26 (patched: `paper_deadline=` throughout; `_parse_deadline_cell` correct) |
| `codegen/05_db_schema.md` | `cfp/db.py` | 2026-04-26 (gap audit: paper_deadline, is_workshop, submission_system, sponsor_names, quality_flags, scrape_sessions table, sites.last_cursor) |
| `codegen/09_llm_client.md` | `cfp/llm/client.py` + `tools.py` | 2026-04-26 (gap audit: single OLLAMA_HOST, get_available_models, profile_intersection) |
| `codegen/11_analytics_generate.md` | `cfp/analytics.py` + `generate_md.py` | 2026-04-26 (patched: `paper_deadline::VARCHAR` in SQL; all deadline refs updated) |

### Codegen specs — NOT YET WRITTEN
| Spec | Module |
|---|---|
| `codegen/02` | `cfp/prompts_parser.py` |
| `codegen/03` | `cfp/fetch.py` |
| `codegen/06` | `cfp/graph.py` (Apache AGE) |
| `codegen/07` | `cfp/queue.py` (Redis) |
| `codegen/08` | `cfp/vectors.py` + `cfp/embed.py` |
| `codegen/10` | `cfp/llm/tier1..4.py` |
| `codegen/12` | `cfp/pipeline.py` + `cfp/cli.py` |
| `codegen/13` | `setup.sh` + `docker-compose.yml` + `Makefile` |
| `codegen/14` | `AGENTS.md` + `PATTERNS.md` |
| `codegen/15` | `cfp/dedup.py` |
| `codegen/16` | `cfp/sync.py` |
| `codegen/17` | `cfp/ontology.py` |

### Implementation — NOT YET STARTED
`cfp/` package does not exist. All modules are unimplemented.

---

## Blocking Arch Questions (must resolve before coding these modules)

| Question | Blocks | Status |
|---|---|---|
| Q4 — AGE consistency (derived tables vs authoritative) | `cfp/graph.py` | open (v2) |
| Q6 — Cross-source dedup trigger timing | `cfp/dedup.py` | open (v2 needs LLM confirmation; v1 uses pgvector-only) |
| Q10 — Ollama model storage (named volume vs re-pull) | `docker-compose.yml`, `setup.sh` | ✅ **RESOLVED 2026-04-29** — bind mount `/mnt/d/wsl/ollama:/root/.ollama` |
| Q12 — JSON-mode failure retry budget | `cfp/llm/client.py` | ✅ **RESOLVED 2026-04-29** — local repair → 1 retry → escalate; `JSON_RETRY_SAME_TIER=1` |
| Q14 — Quantisation policy per CFP_MACHINE profile | `config.py` | ✅ **RESOLVED 2026-04-29** — pinned q4_K_M tags (q8_0 on dgx) in `PROFILE_MODELS` |
| Q15 — Workshop: `is_workshop` flag vs `Workshop` graph node | `cfp/models.py`, `cfp/graph.py` | ✅ **RESOLVED 2026-04-26** — `is_workshop` flag on Event; `Workshop` graph node dropped from v1 |

Full question details + decisions: `arch.md §1`

---

## Priority To-Do List

> Full detail in `memory/project_cfp.md`. Summary here for quick reference.

### P0 — Blockers ✅ ALL RESOLVED (2026-04-29)
- [x] **Q10** — Ollama named volume `ollama_models:/root/.ollama` added to `docker-compose.yml`
- [x] **Q12** — JSON repair → 1 retry → escalate; `JSON_RETRY_SAME_TIER=1` in `config.py`
- [x] **Q14** — Quant tags pinned in `PROFILE_MODELS` (q4_K_M default, q8_0 on dgx)
Full answers: `arch.md §1`

### Infrastructure ✅ RUNNING (2026-04-29)
- [x] Docker Desktop WSL2 integration enabled; context set to `default` (Unix socket); `DOCKER_CONTEXT=default` in `~/.bashrc`
- [x] `cfp_postgres` — pgvector/pgvector:pg16, pgvector 0.8.2 installed, healthy, DSN `postgresql://cfp:cfp@localhost:5432/cfp`
- [x] `cfp_redis` — redis:7-alpine with `--appendonly yes` (AOF persistence), healthy
- [x] `cfp_ollama` — ollama/ollama, **GPU enabled** (RTX 3080 Ti Laptop, 16 GB VRAM), bind mount `/mnt/d/wsl/ollama:/root/.ollama` (5.8 GB used)
- [x] `rclone v1.73.5` installed at `~/.local/bin/rclone`
- [ ] GCS / rclone remote configured — pending bucket name + GCP project ID from user

### Hardware (verified 2026-04-29)
- GPU: NVIDIA GeForce RTX 3080 Ti Laptop GPU, 16 GB VRAM, driver 581.95, CUDA 13.0
- D: drive (`/mnt/d/`): 248 GB free — used for Ollama models
- Effective VRAM ceiling for inference: ~14 GB (KV cache + overhead)
- `CFP_MACHINE=gpu_mid` profile is the right starting point; some 22B Q4 models fit (Devstral, Codestral)

### Models pulled
| Model | Size | Notes |
|---|---|---|
| `nomic-embed-text:latest` | 274 MB | Embeddings — used by all profiles |
| `qwen3:4b` | 2.5 GB | **TO REMOVE** — superseded by qwen3.5:4b |
| `qwen3.5:4b` | 3.4 GB | Tier 1 triage |

### Models to pull next (per `evals.md` §8 — eval-backed picks for 16 GB)
- [ ] `qwen2.5-coder:14b` (~10 GB) — primary local code model
- [ ] `deepseek-r1:14b` (~8.8 GB) — Tier 4 reasoning + debugging
- [ ] `deepseek-coder-v2:16b` (~9 GB) — long-context (128K) MoE
- [ ] `codestral:22b` (~12 GB) — FIM autocomplete + HLS/VHDL
- [ ] `devstral-small-2:24b` (~15 GB) — agentic coding (tight fit; verify)
- [ ] `codev-r1-rl-qwen-7b` (~5 GB, HuggingFace GGUF) — Verilog/RTL specialist

### P1 — Write missing v1 codegen specs
- [ ] `codegen/02` — `cfp/prompts_parser.py`
- [ ] `codegen/03` — `cfp/fetch.py` (aiohttp, not requests — arch.md S13)
- [ ] `codegen/07` — `cfp/queue.py` (Redis)
- [ ] `codegen/08` — `cfp/vectors.py` + `cfp/embed.py`
- [ ] `codegen/13` — `docker-compose.yml` (`pgvector/pgvector:pg16`) + `Makefile`
- [ ] `codegen/15` — `cfp/dedup.py` (pgvector-only for v1)
- [ ] `codegen/16` — `cfp/sync.py` (GCS pull/push)
- [ ] `codegen/10` — `cfp/llm/tier1.py` + `tier2.py`
- [ ] `codegen/12` — `cfp/pipeline.py` + `cfp/cli.py`

### P2 — Patch stale written specs ✅ COMPLETE
- [x] Spec 04: `paper_deadline=` throughout — done 2026-04-26
- [x] Spec 11: `paper_deadline::VARCHAR` in SQL — done 2026-04-26

### P3 — Ontology seed (small, hand-authored)
- [ ] Create `ontology/seed_concepts.json` (13 Category values + ~50 subconcepts)
- [ ] Add `bootstrap-ontology` CLI command to spec 12

### P4 — Implement v1 (strict dependency order)
```
spec 01  config.py + cfp/models.py          ← START HERE
spec 02  cfp/prompts_parser.py
spec 03  cfp/fetch.py
spec 04  cfp/parsers/wikicfp.py + ai_deadlines.py
spec 05  cfp/db.py
spec 07  cfp/queue.py
spec 08  cfp/vectors.py + cfp/embed.py
spec 09  cfp/llm/client.py + tools.py
spec 10  cfp/llm/tier1.py + tier2.py
spec 15  cfp/dedup.py
spec 16  cfp/sync.py
spec 12  cfp/pipeline.py + cfp/cli.py
spec 13  docker-compose.yml + Makefile
spec 11  cfp/analytics.py + generate_md.py
         → Delete scraper.py after parsers/wikicfp.py verified
```

### P5 — v1 validation + completion
- [ ] Run v1 weekly for 1 month with real data
- [x] lesson_plan.md Modules 14–35 — done 2026-04-26 (async, BS4, HTTP, date, Docker, git, ethics, testing, packaging, type hints, regex, YAML, CLI, logging, rclone, Makefile, conference ecosystem, OWL, pgBouncer, concurrency, backoff)
- [ ] `tests/` directory with pytest fixtures from real WikiCFP HTML

### P6 — Enhancements (post-v1)
- [ ] Gmail integration (`cfp/parsers/email_gmail.py`)
- [ ] EDAS / EasyChair / OpenReview / HotCRP parsers
- [ ] Health check FastAPI endpoint
- [ ] Predatory publisher blocklist
- [ ] Prometheus + Grafana observability

### P7 — v2 (additive migration, no rewrites)
- [ ] Switch Docker image → `apache/age:PG16_latest`, implement `cfp/graph.py`
- [ ] Tier 3 + 4, DeepSeek-R1 dedup confirmation
- [ ] DuckDB analytics layer, ontology pipeline (`cfp/ontology.py`)
- [ ] Kubernetes manifests (`arch.md §5` — ~$85/mo on GKE)

---

## Key Constraints (never violate)

1. `models.py` and `config.py` import NOTHING project-internal
2. All writes → PostgreSQL via psycopg3 (NOT psycopg2, NOT DuckDB)
3. DuckDB is read-only analytics — never writes to disk
4. Redis stores zero business data
5. Tool calling: Qwen3 ONLY. DeepSeek-R1 = pure reasoning, no tools
6. WikiCFP paired-row parsing in `scraper.py` is CORRECT — copy verbatim to `cfp/parsers/wikicfp.py`
7. India state-wise location taxonomy in `generate_md.py` is CORRECT — copy verbatim
8. COALESCE upsert: never overwrite notification, camera_ready, rank, notes, submission_system, sponsor_names, official_url, description with NULL. **Always overwrite** (direct update): paper_deadline, abstract_deadline, dates, location, quality_flags, quality_severity, scrape_session_id
9. Crawl delays: min 5s, Gaussian(8, 2.5), 10% chance 15–45s pause
10. **Canonical field name is `paper_deadline`** — not `deadline`. Used uniformly across `models.Event`, `events.paper_deadline` column, prompts.md, parsers, Markdown output. The legacy name `deadline` must not appear in new code.
11. **Single Ollama daemon per machine** at `OLLAMA_HOST` — no per-model host routing. Tier escalation handles model availability via `PROFILE_MODELS[CFP_MACHINE]`.

---

## Patterns to Carry Forward (from conf-scr-org-syn repo)

| Pattern | Destination |
|---|---|
| `_is_english()` filter | `cfp/parsers/wikicfp.py` |
| `_safe_parse_date()` dateutil fuzzy | `cfp/parsers/wikicfp.py` |
| Abstract + paper deadline regex | `cfp/parsers/wikicfp.py` |
| COALESCE upsert | `cfp/db.py` |
| `_parse_json_response()` 3-level fallback | `cfp/llm/client.py` |
| `_strip_thinking()` | `cfp/llm/client.py` |
| `slug` + `days_to_deadline` properties | `cfp/models.py` |
| `to_markdown()` | `cfp/models.py` |
| `## Notes` preservation | `generate_md.py` |
| `scrape_ai_deadlines()` YAML | `cfp/parsers/ai_deadlines.py` |
| Rich CLI deadline coloring | `cfp/cli.py` |
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
