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
- New: any single machine, WCFP_MACHINE profile, single OLLAMA_HOST=localhost
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
