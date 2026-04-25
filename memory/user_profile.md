---
name: Raja — User Profile
description: Technical background, hardware, working style, preferences
type: user
---

## Hardware
- RTX 4090 24 GB VRAM — Workstation A (primary inference, tool calling tier 3)
- RTX 3080 16 GB VRAM — Workstation B (tiers 1–2, embeddings)
- DGX Station ~256 GB GPU — 8× A100 (tier 4 overnight batch, ontology)
- All machines run Ollama daemons, addressed via OLLAMA_HOSTS in config.py

## Technical Depth
Comfortable with: PostgreSQL internals, graph databases (Apache AGE, Neo4j), LLM pipeline design, ontology engineering (OWL, RDF, SPARQL), distributed systems, Python, Docker, Linux kernel/systems.

Not afraid to ask "why not X?" — expects honest first-principles tradeoff analysis, not validation.

## Working Style
- Builds systems across multiple sessions; uses MD files to preserve context between sessions
- Designs architecture first (context.md, codegen/ specs), implements later with Sonnet
- Likes deep reasoning discussions ("think and reason it well" = go first-principles)
- Stops sessions deliberately: "stop" means push what exists and end cleanly
- Runs shell in WSL2 (Linux 6.6.87 on Windows), bash shell

## Repo Preferences
- Short lowercase repo names (wiki-cfp → cfp)
- GitHub username: rajaghv-dev
- Email: rajaghv.dev@gmail.com
- Prefers `gh` CLI for GitHub operations (authenticated)

## Communication Preferences
- Concise updates while working (one sentence)
- Deep explanations when explicitly asked
- No unsolicited refactoring or feature additions
- No trailing summaries of what was just done
