---
name: Conversation log — key decisions per session
description: Running log of what changed and why each session
type: project
---

## Session 1 — 2026-04-25

### Started with
Basic WikiCFP scraper repo (`wiki-cfp`) — BS4 scraper + generate_md.py for Markdown reports.

### Key decisions
- PostgreSQL + pgvector + Apache AGE chosen (only stack unifying vector+graph+relational in one query)
- DuckDB demoted to analytics-only (postgres_scanner, no storage)
- Redis for queue + rate limiting only (zero business data)
- Qwen3 for ALL tool calling; DeepSeek-R1 for pure reasoning
- 4-tier cascade: 4b→14b→32b→70b, confidence-gated escalation
- People, Venues, Orgs as first-class graph nodes
- Ontology: bottom-up from raw_tags, AGE IS the live ontology
- conf-scr-org-syn patterns analysed and documented in codegen/ specs
- Repo renamed: wiki-cfp → cfp

### Files created
context.md (19 sections), prompts.md (7 prompts), SESSION.md, setup.sh, memory/, codegen/01,04,05,09,11

---

## Session 2 — 2026-04-26

### Major architectural change
**Multi-machine → single-machine + GCS**
- Old: RTX 3080 + RTX 4090 + DGX running concurrently, OLLAMA_HOSTS routing dict
- New: any single machine, CFP_MACHINE profile, single OLLAMA_HOST=localhost
- GCS = off-machine persistence (pg_dump + rclone)
- Machine lifecycle: pull → restore → run → sync → wipe

### Arch docs overhauled
- context.md: §2 (hardware → single-machine profiles), §3 (GCS layer added), §9 (model roster simplified), §12 (config simplified), §15 (install updated), new §18 (machine lifecycle), new §19 (open questions), new §20 (risks)
- arch.md created (1,477 lines): 15 open questions, 18 risks, 8 ADRs, 15 suggestions, K8s spec, v1/v2 scope split
- CLAUDE.md created (78 lines): standing session instructions

### Prompts overhauled (Sonnet then Opus deep rewrite)
- 12 prompts total (was 7): TIER1–4, DEDUP, ONTOLOGY_SYNONYM, ONTOLOGY_ISA, PERSON_EXTRACT, VENUE_EXTRACT, QUALITY_GUARD, SERIES_EXTRACT, ORG_EXTRACT, DEADLINE_CHANGE
- New fields: paper_deadline (renamed from deadline), abstract_deadline, is_workshop, submission_system, sponsor_names, description, rank
- New parsers added: EDAS, EasyChair, HotCRP, CMT, OpenReview

### lesson_plan.md created (912 lines)
14 modules: PostgreSQL, pgvector, Apache AGE, DuckDB, Redis, LLM concepts, 4-tier pipeline, ontology, scraping, dedup, GCS lifecycle, Kubernetes, observability + A–Z glossary

### Gap audit (Opus)
Stale codegen specs fixed: 01 (single OLLAMA_HOST, paper_deadline, is_workshop, sponsor_names), 05 (new columns, scrape_sessions table), 09 (OLLAMA_HOST import)
.env.example created
v1 scope formally defined in arch.md §6: Tiers 1+2, pgvector only, pgvector-only dedup, no DuckDB, no AGE

### Kubernetes analysis
Full K8s architecture in arch.md §5: node pools, KEDA scaling, Workload Identity, ~$85/mo on GKE
Decision: Docker Compose for v1, K8s manifests written alongside for v2

### Ontology confirmed as v2 feature
Strategy fully designed (context.md §14, prompts ONTOLOGY_SYNONYM + ISA + TIER4)
Seed file (ontology/seed_concepts.json) identified as missing piece to create before v2

### Priority to-do list compiled
P0 (Q10/Q12/Q14 blockers) → P1 (missing specs) → P2 (patch stale specs) → P3 (ontology seed) → P4 (v1 impl) → P5 (validation) → P6 (enhancements) → P7 (v2)

### Memory unified into repo
All memory files moved to memory/ in the repo. Local .claude/ memory mirrors repo.

---

## Session 3 — 2026-04-29

### P0 arch blockers all resolved (Q10, Q12, Q14)
- Q10 → Ollama bind mount `/mnt/d/wsl/ollama:/root/.ollama` (Windows D: drive, 248 GB free)
- Q12 → Local JSON repair → 1 same-tier retry → escalate; `JSON_RETRY_SAME_TIER=1`, `JSON_REPAIR_ENABLED=True` added to config.py spec
- Q14 → `PROFILE_MODELS` updated with pinned per-profile quant tags (q4_K_M default, q8_0 on dgx)

### Repo-wide identifier rename (wcfp/wikicfp → cfp)
- Renamed across 22 files via perl script: package paths (`wcfp/` → `cfp/`), Python imports (`wcfp.` → `cfp.`), Redis keys (`wcfp:` → `cfp:`), env vars (`WCFP_` → `CFP_`), AGE graph (`wcfp_graph` → `cfp_graph`), DB/user (`wikicfp`/`wcfp` → `cfp`), field name (`wikicfp_url` → `origin_url`)
- Preserved: `wikicfp.com` URLs, `WikiCFP` proper noun, `cfp/parsers/wikicfp.py` filename (named after source it parses)
- Standing instruction added to memory/feedback_style.md: use `cfp` only

### Infrastructure brought up (Docker stack on WSL2)
- Docker Desktop WSL2 integration enabled; required `docker context use default` because `desktop-linux` context uses Windows named pipe (doesn't work from WSL2). Persisted via `DOCKER_CONTEXT=default` in `~/.bashrc`.
- `docker-compose.yml` written: postgres+pgvector + redis+AOF + ollama+GPU
- Stack running: postgres 16 + pgvector 0.8.2, redis 7-alpine with AOF, ollama with GPU passthrough (RTX 3080 Ti Laptop, 16 GB)
- Ollama bind-mounted to `/mnt/d/wsl/ollama` (avoids WSL VHD constraint; 5.8 GB used)
- rclone v1.73.5 installed at `~/.local/bin/rclone` (no sudo)
- Native PG install fallback script written: `scripts/setup_postgres.sh`

### Model research → evals.md
- Researched best open-source code/reasoning/RTL models for 16 GB VRAM
- 5 web searches + 3 page fetches; documented all sources in evals.md
- Top picks for this hardware (eval-backed):
  - `devstral-small-2:24b` (15 GB) — best agentic coding that fits
  - `deepseek-coder-v2:16b` (9 GB) — long-context MoE
  - `qwen2.5-coder:14b` (10 GB) — primary local code
  - `deepseek-r1:14b` (8.8 GB) — reasoning/debugging
  - `codestral:22b` (12 GB) — FIM autocomplete
  - `codev-r1-rl-qwen-7b` (5 GB, HF GGUF) — Verilog/RTL specialist
- Models that DON'T fit (rejected): GLM-5, Kimi K2.5 (623 GB!), Qwen3.5-397B, DeepSeek V4

### Models pulled so far
- `nomic-embed-text:latest` (274 MB)
- `qwen3:4b` (2.5 GB) — to be removed (qwen3.5 supersedes)
- `qwen3.5:4b` (3.4 GB) — Tier 1

### Files created this session
- `docker-compose.yml`, `evals.md`, `scripts/setup_postgres.sh`, `.env`

### Standing behaviour additions to feedback memory
- Use `cfp` only — never `wcfp` or `wikicfp` for internal identifiers
- Save SESSION.md and memory files periodically during a session, not just at end
