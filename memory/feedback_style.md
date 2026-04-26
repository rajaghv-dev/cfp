---
name: Standing behaviours and feedback
description: How Claude must behave in every session — confirmed through explicit instruction
type: feedback
---

## Push to GitHub after every meaningful change
Always: `git add <specific files> && git commit -m "<message>" && git push origin main`
Never use `git add .` or `git add -A`.
**Why:** User confirmed as standing requirement — no unpushed commits sitting locally.

## Use Opus for deep work, Sonnet for routine tasks
- Opus: writing/rewriting LLM prompts, deep arch analysis, arch.md content, lesson_plan.md, trade-off evaluation
- Sonnet: file edits, git operations, quick lookups, SESSION.md updates
**Why:** User asked "use your best model" mid-session; Opus rewrites were demonstrably better.

## All new LLM prompts go into prompts.md immediately
Every PROMPT_* discussed or agreed upon → added to prompts.md before session ends.
New parser domain → KNOWN PARSERS section. New data source URL → EXTERNAL DATA SOURCES section.
**Why:** User noticed prompts being discussed but not filed: "hope u r adding all my prompts in prompts.md".

## All context/memory files must be in the repo
Nothing left only on the local machine. Memory files live in `memory/` (tracked by git).
On every session end: push memory/ + SESSION.md.
**Why:** User instruction: "make all these files as part of the repo, dont leave anything to local machine."

## Document every architectural decision
Every decision → row in `context.md §17`. Resolved arch question → mark RESOLVED in `arch.md §1`. New ADR → append to `arch.md §3`.
**Why:** Future sessions must not re-debate settled questions.

## Do not implement before arch questions are resolved
Before any code in `wcfp/`, verify the relevant `arch.md §1` question is marked RESOLVED.
**Why:** User restructured the todo list explicitly: "before codegen, need to understand n fix arch."

## Update SESSION.md and memory/ at end of every session
SESSION.md = single source of truth for current state. Memory files = context for next session.
**Why:** User instruction: "update sessions n memory md files."

## Standing session instructions live in CLAUDE.md
CLAUDE.md is auto-loaded by Claude Code. If user adds a new standing instruction, add it to CLAUDE.md + memory/feedback_style.md immediately.
**Why:** User: "add set of instructions prompts so that you can start your next sessions without repeating prompts."

## Comprehensive over quick for documentation tasks
Go deep when the task is docs, analysis, or prompts. Be terse for file edits and git ops.
**Why:** User accepted and built on thorough outputs (1,477-line arch.md, 912-line lesson_plan.md).

## Stop when asked — do not finish speculative work
"stop" = commit what exists and end. Do not add "one more thing."
**Why:** Sessions are deliberate. Over-generating creates review debt.
