# cfp — Conference Knowledge Pipeline

Scrape, classify, deduplicate, and query Call-for-Papers across the global
academic conference circuit, with an LLM-curated knowledge graph and
auto-generated Markdown reports.

---

## What this is

`cfp` is a self-hosted pipeline that scrapes [WikiCFP](http://www.wikicfp.com),
[ai-deadlines](https://github.com/paperswithcode/ai-deadlines), Gmail CFP
mailing lists, and other CFP sources, then classifies every event through a
4-tier LLM cascade (Qwen3 4b → 14b → 32b → DeepSeek-R1) running locally on
Ollama. Events, people, venues, and organisations are stored as first-class
entities in PostgreSQL 16 with pgvector embeddings (and, in v2, an Apache AGE
property graph for ontology + PC-chair-network queries). Markdown reports are
regenerated from the database on every run, organised by category, region, and
deadline. All persistent state lives in Google Cloud Storage; any single
machine can pull state, run a session, sync back, and wipe local data without
data loss.

---

## Quick start

```bash
git clone https://github.com/rajaghv-dev/cfp.git
cd cfp
CFP_MACHINE=gpu_large GCS_BUCKET=cfp-data bash setup.sh
source .venv/bin/activate

# v1 (current): standalone scraper still works
python3 scraper.py

# v1 (after cfp/ package lands)
python -m cfp init-db
python -m cfp enqueue-seeds
python -m cfp run-pipeline
python -m cfp generate-reports
```

`setup.sh` provisions venv + pip, brings up Docker (PostgreSQL 16 + Redis),
pulls the Ollama models for your `CFP_MACHINE` profile, and restores the
latest `pg_dump` from GCS via rclone.

Full lifecycle (pull → run → sync → wipe) is documented in `context.md §18`.

---

## Machine profiles

`CFP_MACHINE` controls which LLM tiers run locally. Jobs whose required model
is absent get pushed to `cfp:escalate:tier4` and accumulate until the next
session on a capable machine — nothing is lost.

| `CFP_MACHINE` | Min VRAM | Tiers / Models                                                     |
|----------------|----------|--------------------------------------------------------------------|
| `dgx`          | 80 GB    | All tiers + `deepseek-r1:70b` for Tier 4                           |
| `gpu_large`    | 24 GB    | Tiers 1–4 (`qwen3:4b` + `qwen3:14b` + `qwen3:32b` + `deepseek-r1:32b`) |
| `gpu_mid`      | 10 GB    | Tiers 1–2 (`qwen3:4b` + `qwen3:14b`)                               |
| `gpu_small`    | 4 GB     | Tier 1 only (`qwen3:4b`)                                           |
| `cpu_only`     | —        | Tier 1 only (`qwen3:4b`, slow)                                     |

`nomic-embed-text` runs on every profile (~300 MB VRAM or CPU fallback).

---

## Architecture

Three databases, three completely different roles. None can substitute for
another.

| Component                                | Role                                                       |
|------------------------------------------|------------------------------------------------------------|
| PostgreSQL 16 + pgvector + Apache AGE    | Source of truth — structured rows + vectors + graph (AGE in v2) |
| DuckDB                                   | Analytics ONLY — reads PG via `postgres_scanner`, never writes |
| Redis                                    | Queue + rate limiting ONLY — zero business data            |
| GCS (rclone)                             | Off-machine persistence — `pg_dump` + Parquet + reports    |

Full spec in `context.md §3`. Open architectural questions in `arch.md §1`.

---

## LLM pipeline

| Tier | Model               | Confidence gate | Role                                         |
|------|---------------------|-----------------|----------------------------------------------|
| 1    | `qwen3:4b`          | ≥ 0.85          | Triage: is_cfp + categories + is_virtual     |
| 2    | `qwen3:14b`         | ≥ 0.85          | Full Event + Person[] + Venue + Organisation[] |
| 3    | `qwen3:32b`         | ≥ 0.80          | Tool calling for unknown external sites      |
| 4    | `deepseek-r1:70b`   | always final    | Overnight batch + ontology inference (v2)    |

Tool calling is Qwen3-only. DeepSeek-R1 is pure reasoning — used for dedup
yes/no decisions and the Tier 4 batch. Embeddings via `nomic-embed-text`
(768-d, written to `event_embeddings`/`concept_embeddings` pgvector columns).

---

## Generated reports (`reports/`)

13 Markdown reports — regenerated on every run.

### By category

| File                     | Contents                                              |
|--------------------------|-------------------------------------------------------|
| `reports/ai.md`          | AI — Artificial Intelligence                          |
| `reports/ml.md`          | ML — Machine Learning & Deep Learning                 |
| `reports/devops.md`      | DevOps & Site Reliability Engineering                 |
| `reports/linux.md`       | Linux & Open Source                                   |
| `reports/chipdesign.md`  | Chip Design — VLSI / EDA / FPGA / Semiconductor       |
| `reports/math.md`        | Mathematics                                           |
| `reports/legal.md`       | Legal, Cyber Law & Intellectual Property              |

### By date

| File                  | Contents                                       |
|-----------------------|------------------------------------------------|
| `reports/by_date.md`  | All conferences sorted by start date           |

### By region

| File                       | Contents                                              |
|----------------------------|-------------------------------------------------------|
| `reports/usa.md`           | USA conferences                                       |
| `reports/europe.md`        | European conferences (incl. UK & Switzerland)         |
| `reports/uk.md`            | UK conferences                                        |
| `reports/singapore.md`     | Singapore conferences                                 |
| `reports/switzerland.md`   | Switzerland conferences                               |
| `reports/india.md`         | India conferences, **organised state-wise**           |

Each report has two sections — **Upcoming** (sorted earliest first) and
**Past** (sorted most-recent first). Past conferences age out of Upcoming
automatically on every run.

---

## Project structure

| File / dir              | Role                                                                         |
|-------------------------|------------------------------------------------------------------------------|
| `CLAUDE.md`             | Standing instructions for Claude Code (auto-loaded)                          |
| `context.md`            | 20-section architecture spec — source of truth for code generation           |
| `arch.md`               | Deep analysis — 15 open questions, 18 risks, 8 ADRs, 12 suggestions, K8s spec |
| `prompts.md`            | All 12 LLM system prompts + search queries + parser registry                 |
| `lesson_plan.md`        | 14-module learning curriculum + A–Z glossary                                 |
| `codegen/`              | One Markdown spec per module — read before implementing                      |
| `memory/`               | Session memory files (travel with the repo; mirror to `~/.claude/`)          |
| `SESSION.md`            | Current session state — read at the start of every session                   |
| `setup.sh`              | Clone + venv + pip + Docker + rclone pull + Ollama pull                      |
| `scraper.py`            | Standalone v1 WikiCFP scraper (deleted once `cfp/parsers/wikicfp.py` lands) |
| `generate_md.py`        | Markdown report generator (replaced by `cfp/analytics.py` driver)           |

---

## Development status

**v1 is in progress.** The `cfp/` package does not yet exist — only the
standalone `scraper.py` + `generate_md.py` are runnable today. Codegen specs
01, 04, 05, 09, 11 are written; specs 02, 03, 06, 07, 08, 10, 12–17 are still
to be authored. Implementation order is documented in `SESSION.md`.

v2 is an additive migration that adds Apache AGE (graph + Cypher),
DeepSeek-R1 dedup confirmation, Tier 3 + Tier 4, the DuckDB analytics layer,
and the OWL ontology pipeline. v2 does not require a rewrite — it switches
the Docker image from `pgvector/pgvector:pg16` to `apache/age:PG16_latest`
and adds the new modules.

---

## Requirements

- Python 3.11+
- Docker (for PostgreSQL + Redis containers)
- Ollama (single local daemon at `OLLAMA_HOST`, default `http://localhost:11434`)
- rclone (for GCS pull/push of `pg_dump` + Parquet snapshots)

Python deps: see `requirements.txt`.

Last setup: 2026-04-29 15:31:35 (machine: local)
