---
name: CFP project — state and priority to-do list
description: Current phase, file inventory, and full priority-ordered to-do list
type: project
---

## Phase
**Documentation and architecture complete. Pre-implementation.**
v1 scope defined in `arch.md §6`. Nothing in `wcfp/` exists yet.

## File Inventory (2026-04-26)

### Working — do not break
| File | Notes |
|---|---|
| `scraper.py` | WikiCFP BS4 scraper — paired-row parser correct |
| `generate_md.py` | India state-wise reports — location taxonomy correct |
| `data/latest.json` | 350-conference seed for PostgreSQL first run |
| `reports/*.md` | 13 regional/category Markdown reports |

### Documentation — complete
| File | Lines | Notes |
|---|---|---|
| `CLAUDE.md` | 78 | Session instructions — auto-loaded by Claude Code |
| `context.md` | 679 | 20-section architecture spec |
| `arch.md` | 1,477 | 15 questions · 18 risks · 8 ADRs · 15 suggestions · K8s spec · v1/v2 scope split |
| `prompts.md` | 1,008 | 12 LLM prompts + search queries + parsers + external sources |
| `lesson_plan.md` | 912 | 14-module learning curriculum + A–Z glossary |
| `SESSION.md` | — | Priority to-do list + current state |
| `.env.example` | 35 | All env vars with defaults |

### Codegen specs — written (reviewed 2026-04-26)
| File | Covers | Known issues |
|---|---|---|
| `codegen/01` | `config.py` + `wcfp/models.py` | Clean — single OLLAMA_HOST, paper_deadline, is_workshop |
| `codegen/04` | `wcfp/parsers/` | Still emits `Event(deadline=…)` — patch before implementing |
| `codegen/05` | `wcfp/db.py` | Clean — all new columns, scrape_sessions table |
| `codegen/09` | `wcfp/llm/client.py` + `tools.py` | Clean — single OLLAMA_HOST import |
| `codegen/11` | `wcfp/analytics.py` + `generate_md.py` | SQL uses `deadline::VARCHAR` — patch before implementing |

### Codegen specs — NOT written yet
`02` (prompts_parser) · `03` (fetch.py) · `07` (queue.py) · `08` (vectors+embed) ·
`10` (tier1+2) · `12` (pipeline+cli) · `13` (docker-compose+Makefile) ·
`15` (dedup.py) · `16` (sync.py) · v2-only: `06` (graph.py) · `17` (ontology.py)

---

## Priority To-Do List

### P0 — Blockers: resolve before writing any code
These 3 open questions hit v1 modules directly. Answers + recommendations are in `arch.md §1`.

| # | Question | Blocks |
|---|---|---|
| Q10 | Ollama model storage: named Docker volume vs re-pull each session | `docker-compose.yml`, `setup.sh` |
| Q12 | JSON-mode failure: retry budget and escalation path | `wcfp/llm/client.py` |
| Q14 | Quantisation policy: pin quant tags per `PROFILE_MODELS` entry | `config.py` |

---

### P1 — Write missing v1 codegen specs
In order (each may depend on the previous):

- [ ] `codegen/02` — `wcfp/prompts_parser.py`
- [ ] `codegen/03` — `wcfp/fetch.py` (use aiohttp per arch.md S13, not requests)
- [ ] `codegen/07` — `wcfp/queue.py` (Redis — sorted set, SETNX, inflight lease)
- [ ] `codegen/08` — `wcfp/vectors.py` + `wcfp/embed.py` (pgvector + nomic-embed-text)
- [ ] `codegen/13` — `docker-compose.yml` (pgvector/pgvector:pg16 image, NOT apache/age) + `Makefile` with full lifecycle targets
- [ ] `codegen/15` — `wcfp/dedup.py` (pgvector-only for v1; LLM confirmation is v2)
- [ ] `codegen/16` — `wcfp/sync.py` (GCS pull/push via rclone + pg_dump/restore)
- [ ] `codegen/10` — `wcfp/llm/tier1.py` + `tier2.py` (v1 tiers only)
- [ ] `codegen/12` — `wcfp/pipeline.py` + `wcfp/cli.py`

---

### P2 — Patch known issues in written specs
- [ ] Spec 04: rename all `Event(deadline=…)` → `Event(paper_deadline=…)` throughout
- [ ] Spec 11: rename `deadline::VARCHAR` → `paper_deadline::VARCHAR` in SQL queries

---

### P3 — Ontology seed (before v2, but small and hand-authored)
- [ ] Create `ontology/seed_concepts.json` — 13 Category values + ~50 subconcepts as seed hierarchy
- [ ] Add `bootstrap-ontology` CLI command spec to codegen

---

### P4 — Implement v1 (in strict dependency order)
- [ ] `config.py` + `wcfp/models.py` ← spec 01
- [ ] `wcfp/prompts_parser.py` ← spec 02
- [ ] `wcfp/fetch.py` ← spec 03
- [ ] `wcfp/parsers/wikicfp.py` + `ai_deadlines.py` ← spec 04
- [ ] `wcfp/db.py` ← spec 05
- [ ] `wcfp/queue.py` ← spec 07
- [ ] `wcfp/vectors.py` + `wcfp/embed.py` ← spec 08
- [ ] `wcfp/llm/client.py` + `tools.py` ← spec 09
- [ ] `wcfp/llm/tier1.py` + `tier2.py` ← spec 10
- [ ] `wcfp/dedup.py` ← spec 15
- [ ] `wcfp/sync.py` ← spec 16
- [ ] `wcfp/pipeline.py` + `wcfp/cli.py` ← spec 12
- [ ] `docker-compose.yml` + `Makefile` ← spec 13
- [ ] `wcfp/analytics.py` + `generate_md.py` ← spec 11
- [ ] **Delete `scraper.py`** after `wcfp/parsers/wikicfp.py` verified working

---

### P5 — v1 completion and validation
- [ ] Run v1 with real data weekly for 1 month
- [ ] Add lesson_plan.md Modules 14–21 (async Python, BS4, HTTP semantics, date parsing, Docker, git workflow, scraping ethics, testing strategy)
- [ ] Write `tests/` directory: pytest fixtures from real WikiCFP HTML, contract tests per LLM prompt schema

---

### P6 — Post-v1 enhancements (after real data validates v1)
- [ ] Gmail integration — `wcfp/parsers/email_gmail.py` (Gmail API OAuth2)
- [ ] EDAS / EasyChair / OpenReview / HotCRP parsers
- [ ] Health check FastAPI endpoint (`/healthz`, queue depth, tier metrics)
- [ ] Predatory publisher blocklist (domain blocklist checked before enqueue)
- [ ] Prometheus + Grafana observability stack

---

### P7 — v2 scope (additive migration, no rewrites)
- [ ] Switch Docker image: `pgvector/pgvector:pg16` → `apache/age:PG16_latest`
- [ ] Implement `wcfp/graph.py` (Apache AGE sync, Cypher helper) ← spec 06
- [ ] Implement `wcfp/llm/tier3.py` + `tier4.py` (qwen3:32b tool-calling, deepseek-r1 batch)
- [ ] DeepSeek-R1 dedup confirmation (upgrade `wcfp/dedup.py`)
- [ ] Replace direct PG queries in `analytics.py` with DuckDB postgres_scanner
- [ ] Implement `wcfp/ontology.py` (AGE → owlready2 → .owl export) ← spec 17
- [ ] Kubernetes manifests (see `arch.md §5` for full spec — ~$85/mo on GKE)
