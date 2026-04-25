---
name: Conversation log — key decisions and context
description: Running log of topics covered and decisions made across sessions
type: project
---

## Session 1 — 2026-04-25

### Started with
WikiCFP scraper repo (`wiki-cfp`) — basic Python scraper with BS4, generate_md.py for Markdown reports.

### Decisions made (in order)

**Scraping:**
- WikiCFP has no API — HTML scraping only
- Crawl delay: min 5s, human-like Gaussian timing
- Dedup key: WikiCFP `eventid=N` (globally unique per edition)
- Pagination: WikiCFP uses `?conference=X&page=N`
- Series index: `/cfp/series?t=c&i=A` through Z (~3000+ series)

**Models (after deep hardware discussion):**
- Hardware: RTX 4090 (24 GB), RTX 3080 (16 GB), DGX (256 GB)
- Qwen3 chosen for ALL tool calling (only reliable Ollama family, April 2025)
- DeepSeek-R1 for pure reasoning (best accuracy, no tool overhead)
- Phi-4-reasoning rejected: does NOT support tool calling
- Mistral-NeMo:12b for long-context HTML (128k window)
- 4-tier pipeline designed: 4b→14b→32b→70b, route by confidence

**Database (after pgvector discussion):**
- Rejected DuckDB+LanceDB+Qdrant (fragmented, no cross-modal queries)
- Rejected Neo4j (no vectors, no SQL)
- Chose PostgreSQL + pgvector + Apache AGE
  - Only stack that unifies vector+graph+relational in ONE query
  - DuckDB demoted to analytics-only layer (postgres_scanner, no storage)
  - Redis kept for queue + rate limiting only
- This decision driven by user's request to capture people, PC chairs, venues as first-class entities

**Knowledge graph:**
- People (PC chairs, general chairs, keynote speakers), Venues, Organisations, Cities, Countries all become graph nodes
- Ontology concepts (MachineLearning, VLSI, etc.) as nodes with IS_A/PART_OF/RELATED_TO edges
- Example cross-modal query: pgvector similarity + AGE graph traversal + SQL filter in one statement
- AGE graph IS the live ontology; owlready2/rdflib = export-only for Protégé

**Ontology learning:**
- This project is a natural exercise in ontology engineering
- WikiCFP `Categories:` tags = raw concept candidates
- Pipeline: Tier1 extracts→Tier2 clusters synonyms→Tier3 infers IS_A→Tier4 validates
- Tools: owlready2 (installed), rdflib (installed), Protégé for visualization

**conf-scr-org-syn analysis:**
- Repo: https://github.com/rajaghv-dev/conf-scr-org-syn
- Key patterns extracted: `_is_english()`, `_safe_parse_date()`, abstract+paper deadline regex, COALESCE upsert, `_parse_json_response()` 3-level fallback, `_strip_thinking()`, `slug` property, `to_markdown()`, `## Notes` preservation, `scrape_ai_deadlines()` YAML
- All patterns documented in `codegen/` specs
- Source repo to be deleted after merge into wcfp/ package

**Repo rename:** wiki-cfp → cfp (GitHub: rajaghv-dev/cfp)

**Files created:**
- `context.md` (19-section architecture spec, Opus-reviewed)
- `prompts.md` (search queries + A–Z series index + 7 LLM prompts)
- `SESSION.md` (session continuity)
- `setup.sh` (clone + venv + pip + Ollama pull)
- `memory/` (this directory)
- `codegen/00-05, 09, 11` (partial implementation specs)
- Existing: `scraper.py`, `generate_md.py`, `data/latest.json`, `reports/*.md`

### Open topics (not yet addressed in code)
- Terminology lesson plan requested — not yet created
- Tool calling with web/external sites — discussed but not implemented
- conf-scr-org-syn merge — planned in codegen/ but not implemented
- wcfp/ package — entirely missing, needs codegen/02,03,06-08,10,12-14 first
- docker-compose.yml, Makefile, AGENTS.md, PATTERNS.md — not created

### Next session task
1. Create remaining codegen specs (02, 03, 06–08, 10, 12–14)
2. Implement wcfp/ package using codegen/ specs as prompts for Sonnet
3. Delete scraper.py after wcfp/parsers/wikicfp.py works
