---
name: cfp project — current state
description: Live state of the cfp repo — what exists, what's missing, what's next
type: project
---

## Repo
- **Local:** `/home/raja/wiki-cfp`
- **GitHub:** https://github.com/rajaghv-dev/cfp
- **Branch:** main
- **Clone:** `bash setup.sh https://github.com/rajaghv-dev/cfp.git`

## What Exists and Works
| File | Status |
|---|---|
| `scraper.py` | ✅ WikiCFP BS4 scraper — paired-row parser correct |
| `generate_md.py` | ✅ 13 Markdown reports — India state-wise taxonomy correct |
| `prompts.md` | ✅ 13 categories + A–Z series index + 7 LLM prompt bodies |
| `context.md` | ✅ 19-section architecture spec (settled, complete) |
| `data/latest.json` | ✅ 350-conference seed for PostgreSQL init |
| `reports/*.md` | ✅ 13 regional/category reports, git-tracked |
| `setup.sh` | ✅ Clone + venv + pip + optional Ollama pull (WCFP_MACHINE) |
| `SESSION.md` | ✅ Session continuity file |
| `memory/` | ✅ This directory — synced to repo |

## Codegen Specs Created (for Sonnet implementation)
| File | Covers |
|---|---|
| `codegen/00_HOWTO.md` | How to use codegen files |
| `codegen/01_config_models.md` | `config.py` + `wcfp/models.py` |
| `codegen/04_wikicfp_parser.md` | `wcfp/parsers/` |
| `codegen/05_db_schema.md` | `wcfp/db.py` (full PostgreSQL DDL) |
| `codegen/09_llm_client.md` | `wcfp/llm/client.py` + `tools.py` |
| `codegen/11_analytics_generate.md` | `wcfp/analytics.py` + `generate_md.py` |

## Codegen Specs Still Needed
`codegen/02` (prompts_parser), `codegen/03` (fetch_http), `codegen/06` (graph_age),
`codegen/07` (queue_redis), `codegen/08` (vectors_embed), `codegen/10` (tier_llm),
`codegen/12` (pipeline_cli), `codegen/13` (setup_infra), `codegen/14` (agents_patterns)

## wcfp/ Package — Entirely Missing
Everything in `wcfp/` is still to be created. See `codegen/00_HOWTO.md` for order.

## Also Missing
`config.py`, `docker-compose.yml`, `Makefile`, `AGENTS.md`, `PATTERNS.md`

## Source to Merge
`conf-scr-org-syn` (https://github.com/rajaghv-dev/conf-scr-org-syn) was analyzed.
Key patterns documented in codegen/ specs. Merge happens when wcfp/ is implemented.
The source repo will be deleted after merge is complete.

## How to Apply
Always read SESSION.md first — it has the ordered task list for next session.
Architecture is settled in context.md — do not re-propose alternatives.
