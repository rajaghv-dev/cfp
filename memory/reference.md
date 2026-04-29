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
| `prompts.md` | All 13 LLM system prompts + data sources + parsers |
| `lesson_plan.md` | 35-module learning guide + A–Z glossary |
| `evals.md` | Model research log — what fits 16 GB VRAM, eval-backed picks, FPGA/RTL specialists |
| `.env.example` | All environment variables with defaults and comments |
| `docker-compose.yml` | postgres + redis + ollama (with GPU) — operational compose, not from codegen/13 yet |
| `scripts/setup_postgres.sh` | Native PG16+pgvector install fallback for WSL2 (no Docker) |
| `codegen/00_HOWTO.md` | How to use codegen specs and implementation order |
| `memory/project_cfp.md` | Priority to-do list (this session's primary reference) |

## External systems
- **GCS bucket:** `cfp-data` (env: `GCS_BUCKET`) — **NOT yet configured**, needs user's bucket name + GCP project
- **GCS prefix:** `prod` (env: `GCS_PREFIX`)
- **rclone remote name:** `gcs` (env: `RCLONE_REMOTE`)
- **rclone binary:** `~/.local/bin/rclone` (v1.73.5, installed 2026-04-29)
- **Ollama:** `http://localhost:11434` (single local daemon in Docker, env: `OLLAMA_HOST`)
- **Ollama model storage:** `/mnt/d/wsl/ollama/` (Windows D: drive bind mount, 248 GB free)
- **Postgres DSN:** `postgresql://cfp:cfp@localhost:5432/cfp`
- **Redis URL:** `redis://localhost:6379/0`
- **Docker context:** `default` (Unix socket; `DOCKER_CONTEXT=default` in `~/.bashrc`)
- **User's Google account:** rajaghv-dev (email: rajaghv.dev@gmail.com)
- **Source patterns repo:** https://github.com/rajaghv-dev/conf-scr-org-syn (clone to /tmp if needed)

## Hardware (this machine)
- WSL2 on Windows; Ubuntu 24.04 (noble); kernel 6.6.87.2-microsoft-standard-WSL2
- GPU: NVIDIA GeForce RTX 3080 Ti Laptop, 16 GB VRAM, driver 581.95, CUDA 13.0
- Effective VRAM ceiling for inference: ~14 GB (KV cache + overhead)
- Docker Desktop 4.71.0 with Docker Engine 29.4.1; WSL2 integration enabled

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
| Gmail | Email CFPs | Planned — `cfp/parsers/email_gmail.py` (not yet implemented) |
