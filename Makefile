# cfp — single ops entry point. v1: Docker Compose + host venv.
# K8s deferred to v2 (arch.md §1 Q9).

SHELL          := /usr/bin/env bash
.SHELLFLAGS    := -eu -o pipefail -c
.DEFAULT_GOAL  := help

PYTHON         ?= python3
COMPOSE        ?= docker compose
PROFILE        ?= gpu_mid
PG_CONTAINER   := cfp_postgres
REDIS_CONTAINER := cfp_redis
OLLAMA_CONTAINER := cfp_ollama
PG_USER        := cfp
PG_DB          := cfp
WAIT_TIMEOUT   := 60

.PHONY: help up down wipe ps logs psql redis-cli ollama-list \
        test-extensions test-postgres test \
        init-db seeds run reports doctor \
        sync-pull sync-push models

help:  ## list all targets with descriptions
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST) | sort

# ── Stack lifecycle ─────────────────────────────────────────────────────────
up:  ## bring up postgres + redis + ollama; block until healthy
	@$(COMPOSE) up -d
	@echo "Waiting up to $(WAIT_TIMEOUT)s for cfp_postgres healthy..."
	@for i in $$(seq 1 $(WAIT_TIMEOUT)); do \
	    s=$$(docker inspect --format '{{.State.Health.Status}}' $(PG_CONTAINER) 2>/dev/null || echo starting); \
	    if [ "$$s" = "healthy" ]; then echo "  postgres healthy after $${i}s"; break; fi; \
	    sleep 1; \
	    if [ "$$i" = "$(WAIT_TIMEOUT)" ]; then \
	        echo "  postgres did not become healthy in $(WAIT_TIMEOUT)s"; \
	        $(COMPOSE) logs --tail=30 postgres; exit 1; \
	    fi; \
	done
	@docker exec $(REDIS_CONTAINER) redis-cli ping >/dev/null && echo "  redis PONG"
	@docker exec $(OLLAMA_CONTAINER) ollama list >/dev/null 2>&1 && echo "  ollama responsive" \
	    || echo "  [warn] ollama cold start — try again in a moment"

down:  ## stop stack; PRESERVES volumes (pgdata, redisdata, ollama models)
	@$(COMPOSE) down

wipe:  ## DESTRUCTIVE: removes pgdata + redisdata. Ollama bind mount SAFE.
	@printf '\033[0;31mThis will DESTROY postgres + redis volumes.\033[0m\n'
	@printf 'Ollama models on /mnt/d/wsl/ollama are SAFE (host bind mount).\n'
	@read -r -p "Type 'wipe' to confirm: " ans; \
	    if [ "$$ans" = "wipe" ]; then \
	        $(COMPOSE) down -v; \
	        echo "  pgdata + redisdata removed."; \
	    else \
	        echo "  aborted."; exit 1; \
	    fi

ps:  ## docker compose ps
	@$(COMPOSE) ps

logs:  ## tail logs (Ctrl-C to exit)
	@$(COMPOSE) logs -f

# ── Container shells ────────────────────────────────────────────────────────
psql:  ## open psql against cfp_postgres as cfp/cfp
	@docker exec -it $(PG_CONTAINER) psql -U $(PG_USER) -d $(PG_DB)

redis-cli:  ## open redis-cli against cfp_redis
	@docker exec -it $(REDIS_CONTAINER) redis-cli

ollama-list:  ## list models pulled into cfp_ollama
	@docker exec $(OLLAMA_CONTAINER) ollama list

# ── Smoke tests ─────────────────────────────────────────────────────────────
test-extensions:  ## pgvector + extensions smoke test
	@bash scripts/test_extensions.sh

test-postgres:  ## comprehensive PG smoke test
	@bash scripts/test_postgres.sh

test:  ## full pytest suite (requires `make up`)
	@$(PYTHON) -m pytest -q

# ── CLI verbs (host venv → cfp package) ─────────────────────────────────────
init-db:  ## create schema + extensions; idempotent
	@$(PYTHON) -m cfp init-db

seeds:  ## enqueue all seed URLs from prompts.md
	@$(PYTHON) -m cfp enqueue-seeds

run:  ## run the four-tier pipeline against the current queue
	@$(PYTHON) -m cfp run-pipeline

reports:  ## regenerate markdown reports under reports/
	@$(PYTHON) -m cfp generate-reports

doctor:  ## end-to-end health check
	@$(PYTHON) -m cfp doctor

# ── GCS sync (v2 placeholder) ───────────────────────────────────────────────
sync-pull:  ## (v2) restore latest pg_dump from GCS via cfp/sync.py
	@$(PYTHON) -m cfp sync-pull

sync-push:  ## (v2) pg_dump + push to GCS via cfp/sync.py
	@$(PYTHON) -m cfp sync-push

# ── Ollama models for active profile ────────────────────────────────────────
models:  ## pull Ollama models for $(PROFILE) (cpu_only|gpu_small|gpu_mid|gpu_large|dgx)
	@CFP_MACHINE=$(PROFILE) bash setup.sh --models-only
