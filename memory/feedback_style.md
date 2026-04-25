---
name: Communication and style feedback
description: How Raja wants Claude to behave in this project
type: feedback
---

## Stop When Asked
When Raja says "stop", commit what exists and stop. Do not finish a batch of files, do not create "one more thing while I'm at it."

**Why:** Sessions are deliberate. Over-generating creates review debt and wastes the next session's tokens re-understanding what was auto-created.

## Concise by Default, Deep When Asked
Default: one-sentence updates while working, no trailing summaries.
When Raja says "think and reason it well" or "justify": go first-principles, full tradeoffs, honest analysis. These modes are not contradictory.

**Why:** Raja reads diffs. Summaries of what was just done add no value.

## Architecture Is Settled
Once a decision is in `context.md` or `decisions_arch.md`, do not re-open it. If something genuinely conflicts, flag it in one sentence — don't propose a redesign.

**Why:** Architecture was reasoned through carefully across multiple deep sessions.

## Don't Over-Generate Files
Create files when asked, not speculatively. If a plan calls for 14 files, don't create all 14 in one session unless explicitly asked.

**Why:** Each file needs to be reviewed. Unreviewed files accumulate debt.

## Parallel When Independent
When multiple independent tasks, run them in parallel (parallel Bash calls, parallel Agent launches). Don't serialize work that can overlap.

## Session Files Travel With Repo
Memory files live in `memory/` in the repo AND in the Claude memory system. Always update both.
SESSION.md must reflect the true current state after every push — it's the single source of truth for "where are we?"
