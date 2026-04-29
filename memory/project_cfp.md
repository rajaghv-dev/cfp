---
name: CFP project — state and priority to-do list
description: Current phase, file inventory, and full priority-ordered to-do list
type: project
---

## Phase
**v1 fully implemented and tested.** 170/173 tests passing.
The `cfp/` package now contains 18 Python modules (~5800 LOC source +
~3000 LOC tests). Docker stack healthy. End-to-end live-verified:
`python -m cfp doctor` → all 5 checks green. `make` targets work.

Remaining for v1: real-data run (P5 validation).
Remaining for v2: AGE graph (`cfp/graph.py`), Tier 3+4 modules,
`cfp/sync.py` (GCS), DuckDB analytics layer, ontology pipeline.

## File Inventory (2026-04-29)

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
| `CLAUDE.md` | 80 | Session instructions — auto-loaded by Claude Code |
| `context.md` | 689 | 20-section spec — Q10/Q12/Q14/Q15 RESOLVED in §17 + §19 |
| `arch.md` | 1,485 | 15 questions (Q10/Q12/Q14/Q15 RESOLVED) · 18 risks · 8 ADRs · 15 suggestions · K8s spec |
| `prompts.md` | 1,008 | 13 LLM prompts + search queries + parsers + external sources |
| `lesson_plan.md` | 1,659 | **35-module** curriculum + expanded A–Z glossary |
| `evals.md` | 255 | Model research log: what runs on 16 GB VRAM, eval-backed list, FPGA/HLS specialists |
| `requirements.txt` | 40 | Full v1 deps; v2-only commented out |
| `README.md` | 187 | Current project README with architecture + quick start |
| `SESSION.md` | — | Priority to-do list + current state |
| `.env.example` | 35 | All env vars with defaults |
| `docker-compose.yml` | — | postgres + redis + ollama with GPU + bind mount to D: drive |
| `scripts/setup_postgres.sh` | — | Native PG16+pgvector install fallback for WSL2 |

### Codegen specs — written and reviewed (all reviewed 2026-04-26)
| File | Covers | Status |
|---|---|---|
| `codegen/01` | `config.py` + `cfp/models.py` | ✅ Clean |
| `codegen/04` | `cfp/parsers/` | ✅ Patched — `paper_deadline=` throughout |
| `codegen/05` | `cfp/db.py` | ✅ Clean — all new columns, scrape_sessions table |
| `codegen/09` | `cfp/llm/client.py` + `tools.py` | ✅ Clean — single OLLAMA_HOST |
| `codegen/11` | `cfp/analytics.py` + `generate_md.py` | ✅ Patched — `paper_deadline::VARCHAR` in SQL |

### Codegen specs — NOT written yet
`02` (prompts_parser) · `03` (fetch.py) · `07` (queue.py) · `08` (vectors+embed) ·
`10` (tier1+2) · `12` (pipeline+cli) · `13` (docker-compose+Makefile) ·
`15` (dedup.py) · `16` (sync.py) · v2-only: `06` (graph.py) · `17` (ontology.py)

---

## Priority To-Do List

### P0 — Blockers ✅ ALL RESOLVED (2026-04-29)
| # | Question | Resolution |
|---|---|---|
| Q10 | Ollama model storage | Bind mount `/mnt/d/wsl/ollama:/root/.ollama` (Windows D: drive, 248 GB free) |
| Q12 | JSON-mode failure recovery | Local repair → 1 same-tier retry → escalate; constants in `config.py`: `JSON_RETRY_SAME_TIER=1`, `JSON_REPAIR_ENABLED=True` |
| Q14 | Quantisation policy | Pinned per-profile q4_K_M tags in `PROFILE_MODELS` (q8_0 on dgx) |

### Infrastructure ✅ RUNNING (2026-04-29)
- Docker Desktop WSL2 integration enabled; `DOCKER_CONTEXT=default` in `~/.bashrc`
- `cfp_postgres` (pgvector/pgvector:pg16, pgvector 0.8.2 enabled, healthy)
- `cfp_redis` (redis:7-alpine, AOF persistence on, healthy)
- `cfp_ollama` (GPU passthrough enabled — RTX 3080 Ti Laptop 16 GB VRAM, healthy)
- Ollama bind mount: `/mnt/d/wsl/ollama` (5.8 GB used, models pulled below)
- rclone v1.73.5 installed at `~/.local/bin/rclone`

### Models pulled
- `nomic-embed-text:latest` (274 MB)
- `qwen3:4b` (2.5 GB) — TO REMOVE (superseded)
- `qwen3.5:4b` (3.4 GB) — Tier 1

### Pending infrastructure
- GCS / rclone remote: needs bucket name + GCP project ID from user
- Models to pull next (per `evals.md` §8): `qwen2.5-coder:14b`, `deepseek-r1:14b`, `deepseek-coder-v2:16b`, `codestral:22b`, `devstral-small-2:24b`, `codev-r1-rl-qwen-7b` (HuggingFace GGUF)

---

### P1 — Write missing v1 codegen specs
In order (each may depend on the previous):

- [ ] `codegen/02` — `cfp/prompts_parser.py`
- [ ] `codegen/03` — `cfp/fetch.py` (use aiohttp per arch.md S13, not requests)
- [ ] `codegen/07` — `cfp/queue.py` (Redis — sorted set, SETNX, inflight lease)
- [ ] `codegen/08` — `cfp/vectors.py` + `cfp/embed.py` (pgvector + nomic-embed-text)
- [ ] `codegen/13` — `docker-compose.yml` (pgvector/pgvector:pg16 image, NOT apache/age) + `Makefile` with full lifecycle targets
- [ ] `codegen/15` — `cfp/dedup.py` (pgvector-only for v1; LLM confirmation is v2)
- [ ] `codegen/16` — `cfp/sync.py` (GCS pull/push via rclone + pg_dump/restore)
- [ ] `codegen/10` — `cfp/llm/tier1.py` + `tier2.py` (v1 tiers only)
- [ ] `codegen/12` — `cfp/pipeline.py` + `cfp/cli.py`

---

### P2 — Patch known issues in written specs ✅ COMPLETE (2026-04-26)
- [x] Spec 04: `paper_deadline=` throughout — done
- [x] Spec 11: `paper_deadline::VARCHAR` in SQL — done
- [x] All identifiers renamed: `wcfp/wikicfp` → `cfp` (2026-04-29) — package, Redis keys, env vars, DB name/user, AGE graph name (`wcfp_graph` → `cfp_graph`), field name `wikicfp_url` → `origin_url`. WikiCFP.com URL and proper noun preserved.

---

### P3 — Ontology seed (before v2, but small and hand-authored)
- [ ] Create `ontology/seed_concepts.json` — 13 Category values + ~50 subconcepts as seed hierarchy
- [ ] Add `bootstrap-ontology` CLI command spec to codegen

---

### P4 — Implement v1 (in strict dependency order)
- [ ] `config.py` + `cfp/models.py` ← spec 01
- [ ] `cfp/prompts_parser.py` ← spec 02
- [ ] `cfp/fetch.py` ← spec 03
- [ ] `cfp/parsers/wikicfp.py` + `ai_deadlines.py` ← spec 04
- [ ] `cfp/db.py` ← spec 05
- [ ] `cfp/queue.py` ← spec 07
- [ ] `cfp/vectors.py` + `cfp/embed.py` ← spec 08
- [ ] `cfp/llm/client.py` + `tools.py` ← spec 09
- [ ] `cfp/llm/tier1.py` + `tier2.py` ← spec 10
- [ ] `cfp/dedup.py` ← spec 15
- [ ] `cfp/sync.py` ← spec 16
- [ ] `cfp/pipeline.py` + `cfp/cli.py` ← spec 12
- [ ] `docker-compose.yml` + `Makefile` ← spec 13
- [ ] `cfp/analytics.py` + `generate_md.py` ← spec 11
- [ ] **Delete `scraper.py`** after `cfp/parsers/wikicfp.py` verified working

---

### P5 — v1 completion and validation
- [ ] Run v1 with real data weekly for 1 month
- [x] lesson_plan.md Modules 14–35 — done 2026-04-26 (22 new modules covering async, BS4, HTTP, dates, Docker, git, ethics, testing, packaging, type hints, regex, YAML/JSON, CLI, logging, rclone, Makefile, conference ecosystem, OWL/Protege, pgBouncer, concurrency, backoff)
- [ ] `tests/` directory: pytest fixtures from real WikiCFP HTML, contract tests per LLM prompt schema

---

### P6 — Post-v1 enhancements (after real data validates v1)
- [ ] Gmail integration — `cfp/parsers/email_gmail.py` (Gmail API OAuth2)
- [ ] EDAS / EasyChair / OpenReview / HotCRP parsers
- [ ] Health check FastAPI endpoint (`/healthz`, queue depth, tier metrics)
- [ ] Predatory publisher blocklist (domain blocklist checked before enqueue)
- [ ] Prometheus + Grafana observability stack

---

### P7 — v2 scope (additive migration, no rewrites)
- [ ] Switch Docker image: `pgvector/pgvector:pg16` → `apache/age:PG16_latest`
- [ ] Implement `cfp/graph.py` (Apache AGE sync, Cypher helper) ← spec 06
- [ ] Implement `cfp/llm/tier3.py` + `tier4.py` (qwen3:32b tool-calling, deepseek-r1 batch)
- [ ] DeepSeek-R1 dedup confirmation (upgrade `cfp/dedup.py`)
- [ ] Replace direct PG queries in `analytics.py` with DuckDB postgres_scanner
- [ ] Implement `cfp/ontology.py` (AGE → owlready2 → .owl export) ← spec 17
- [ ] Kubernetes manifests (see `arch.md §5` for full spec — ~$85/mo on GKE)
