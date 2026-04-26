# CLAUDE.md — Project Standing Instructions

> Auto-loaded by Claude Code at session start. Every instruction here applies to
> every session without the user needing to repeat it.

## What This Project Is
A conference knowledge pipeline that scrapes WikiCFP + ai-deadlines + other CFP
sources, classifies entries through a 4-tier LLM cascade (Qwen3 4b/14b/32b +
DeepSeek-R1 70b), and stores everything in PostgreSQL 16 with pgvector
(embeddings) and Apache AGE (knowledge graph), with DuckDB as a read-only
analytics layer and Redis as the operational queue.

## Session Start Protocol
Every session MUST begin with:
1. Read `SESSION.md` — full current state in ~2 minutes
2. Read `arch.md §1` — 15 open questions; do not implement anything that touches an
   unresolved question without first answering it and recording the decision
3. If the user mentions a task not tracked in SESSION.md, ask if it should be added

## Standing Behaviours (apply every session, never ask for permission)

### GitHub
- After ANY meaningful file change: `git add <files> && git commit -m "<message>" && git push origin main`
- Commit messages: imperative mood, specific, e.g. "Add EDAS parser spec to codegen/04"
- Never batch unrelated changes into one commit
- Push after every commit — do not let commits sit unpushed

### prompts.md
- Every new LLM system prompt discussed or agreed upon → add to prompts.md immediately
- Every new data source (URL, parser domain) → add to the KNOWN PARSERS or EXTERNAL DATA SOURCES section
- Every new category keyword → add under the correct CATEGORY block
- After editing prompts.md: verify grammar (2-space indent, no tabs, PROMPT_*: | prefix)

### context.md
- Every architectural decision made in conversation → add a row to §17 Key Decisions Log
- Every new open question identified → add to §19, point to arch.md §1 for full treatment
- Every new risk identified → add to §20

### arch.md
- Every architectural decision resolved → update the relevant Q in §1 (mark RESOLVED, record choice)
- Every new ADR → append to §3
- Every new suggestion → append to §4

### SESSION.md
- At end of every session → update "Last updated" date and "Current File State" table

## Model Selection
- Use **Opus** (`model: opus`) for: deep architectural analysis, writing/rewriting LLM prompts,
  evaluating trade-offs, writing arch.md content, writing lesson_plan.md
- Use **Sonnet** (default) for: file edits, git operations, quick lookups, routine tasks
- Never use Haiku for this project

## Implementation Rules (never violate)
- Do NOT write any code in `wcfp/` until the relevant arch.md §1 question is RESOLVED
- All PostgreSQL writes: `psycopg` (psycopg3) only — never psycopg2, never DuckDB
- DuckDB: read-only analytics layer only — never writes
- Redis: operational data only — zero business data
- Tool calling: Qwen3 models only — DeepSeek-R1 has no tool calling
- Import style: absolute imports (`from wcfp.models import Event`)
- Implement in dependency order: see `codegen/00_HOWTO.md`

## File Ownership Rules
| File | What it is | When to edit |
|---|---|---|
| `prompts.md` | LLM pipeline data — prompts, URLs, keywords, parsers | When adding prompts/sources |
| `context.md` | Architecture spec | When making/recording arch decisions |
| `arch.md` | Deep analysis — questions, risks, ADRs, K8s spec | When resolving questions or adding risks |
| `SESSION.md` | Current session state | At end of every session |
| `CLAUDE.md` | This file — standing instructions | When user adds new standing behaviour |
| `lesson_plan.md` | Learning guide for the user | When adding new concepts or user asks to learn |
| `codegen/*.md` | Code generation specs | Before implementing each module |

## What NOT To Do
- Do not implement a module without a codegen spec
- Do not push breaking changes to a working file (scraper.py, generate_md.py) without explicit instruction
- Do not use `git add .` or `git add -A` — always add specific files
- Do not invent architecture — all decisions must trace to context.md or arch.md
- Do not add comments explaining what code does — only add comments for non-obvious WHY
