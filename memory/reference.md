---
name: Reference locations
description: Where to find things — repo, key files, external systems, data sources
type: reference
---

## Repository
- **GitHub:** https://github.com/rajaghv-dev/cfp
- **Local path:** `/home/raja/cfp`
- **Branch:** `main`
- **Clone + setup:** `bash setup.sh https://github.com/rajaghv-dev/cfp.git`

## Key files — what each one is for
| File | Purpose |
|---|---|
| `CLAUDE.md` | Session instructions — read this first, overrides memory |
| `SESSION.md` | Current state + priority to-do list — always most up-to-date |
| `context.md` | Full architecture spec (20 sections) |
| `arch.md` | Open questions (§1), risks (§2), ADRs (§3), suggestions (§4), K8s spec (§5), v1/v2 split (§6) |
| `prompts.md` | All 12 LLM system prompts + data sources + parsers |
| `lesson_plan.md` | 14-module learning guide + A–Z glossary |
| `.env.example` | All environment variables with defaults and comments |
| `codegen/00_HOWTO.md` | How to use codegen specs and implementation order |
| `memory/project_cfp.md` | Priority to-do list (this session's primary reference) |

## External systems
- **GCS bucket:** `wcfp-data` (env: `GCS_BUCKET`)
- **GCS prefix:** `prod` (env: `GCS_PREFIX`)
- **rclone remote name:** `gcs` (env: `RCLONE_REMOTE`)
- **Ollama:** `http://localhost:11434` (single local daemon, env: `OLLAMA_HOST`)
- **Source patterns repo:** https://github.com/rajaghv-dev/conf-scr-org-syn (clone to /tmp if needed)

## Data sources configured in prompts.md
| Source | Type | URL / location |
|---|---|---|
| WikiCFP | Primary scrape | http://www.wikicfp.com (keyword search + A–Z index) |
| ai-deadlines | YAML | paperswithcode GitHub (URL in prompts.md EXTERNAL DATA SOURCES) |
| IEEE conferences | Browse | conferences.ieee.org |
| EDAS | CFP listings | edas.info/listConferences.php |
| EasyChair | CFP listings | easychair.org/cfp/ |
| CORE rankings | Quality reference | portal.core.edu.au |
| Semantic Scholar | Discovery | API (URL in prompts.md) |
| Gmail | Email CFPs | Planned — `wcfp/parsers/email_gmail.py` (not yet implemented) |
