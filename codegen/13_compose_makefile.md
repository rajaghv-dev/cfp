# Codegen 13 — docker-compose.yml + Makefile

## Files to Manage
- `docker-compose.yml` (already exists — spec documents v1 contract)
- `Makefile` (to be created)
- `scripts/test_postgres.sh` (new — comprehensive PG smoke test)

## Rule
Compose stack defines 3 long-lived services. Makefile is the **single entry
point for ops** in v1. Per arch.md §1 Q9: K8s manifests are deferred to v2.

---

## Part A — docker-compose.yml (reference contract)

| Service | Image (v1) | Container | Port | Volume | Healthcheck |
|---|---|---|---|---|---|
| postgres | `pgvector/pgvector:pg16` | `cfp_postgres` | 5432 | named `pgdata` | `pg_isready -U cfp -d cfp` 10s/5retries |
| redis | `redis:7-alpine` | `cfp_redis` | 6379 | named `redisdata` | `redis-cli ping` 10s/5retries |
| ollama | `ollama/ollama` | `cfp_ollama` | 11434 | bind `/mnt/d/wsl/ollama:/root/.ollama` (Q10) | none in v1 |

**v2 swap**: postgres image → `apache/age:PG16_latest`. Volume layout compatible.

**Postgres**:
- `POSTGRES_USER=cfp`, `POSTGRES_PASSWORD=cfp`, `POSTGRES_DB=cfp`
- `restart: unless-stopped`

**Redis**:
- `command: redis-server --appendonly yes` (AOF on)

**Ollama**:
- BIND mount (not named volume): `/mnt/d/wsl/ollama:/root/.ollama` — survives `make wipe`
- GPU passthrough: `deploy.resources.reservations.devices` with `driver: nvidia`

**Volumes**: `pgdata`, `redisdata` (Ollama bind mount NOT here — host path).

---

## Part B — Makefile

### Header
```make
SHELL          := /usr/bin/env bash
.SHELLFLAGS    := -eu -o pipefail -c
.DEFAULT_GOAL  := help

PYTHON         ?= python3
COMPOSE        ?= docker compose
PROFILE        ?= gpu_mid
PG_CONTAINER   := cfp_postgres
REDIS_CONTAINER:= cfp_redis
OLLAMA_CONTAINER := cfp_ollama
PG_USER        := cfp
PG_DB          := cfp
WAIT_TIMEOUT   := 60

.PHONY: help up down wipe ps logs psql redis-cli ollama-list \
        test-extensions test-postgres test \
        init-db seeds run reports doctor \
        sync-pull sync-push models
```

### Targets
```make
help:  ## list all targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST) | sort

up:  ## bring up postgres + redis + ollama; block until healthy
	@$(COMPOSE) up -d
	@echo "Waiting up to $(WAIT_TIMEOUT)s for cfp_postgres healthy..."
	@for i in $$(seq 1 $(WAIT_TIMEOUT)); do \
	    s=$$(docker inspect --format '{{.State.Health.Status}}' $(PG_CONTAINER) 2>/dev/null || echo starting); \
	    if [ "$$s" = "healthy" ]; then echo "  postgres healthy after $${i}s"; exit 0; fi; \
	    sleep 1; \
	done; \
	echo "  postgres did not become healthy in $(WAIT_TIMEOUT)s"; \
	$(COMPOSE) logs --tail=30 postgres; exit 1
	@docker exec $(REDIS_CONTAINER) redis-cli ping >/dev/null && echo "  redis PONG"
	@docker exec $(OLLAMA_CONTAINER) ollama list >/dev/null 2>&1 && echo "  ollama responsive" \
	    || echo "  [warn] ollama cold start"

down:  ## stop stack; PRESERVES volumes
	@$(COMPOSE) down

wipe:  ## DESTRUCTIVE: removes pgdata + redisdata. Ollama bind mount SAFE.
	@printf '\033[0;31mDESTROYS postgres + redis volumes.\033[0m\n'
	@printf 'Ollama models on /mnt/d/wsl/ollama are SAFE.\n'
	@read -r -p "Type 'wipe' to confirm: " ans; \
	    if [ "$$ans" = "wipe" ]; then \
	        $(COMPOSE) down -v; \
	    else \
	        echo "  aborted."; exit 1; \
	    fi

ps:  ## docker compose ps
	@$(COMPOSE) ps

logs:  ## tail logs
	@$(COMPOSE) logs -f

psql:  ## open psql against cfp_postgres
	@docker exec -it $(PG_CONTAINER) psql -U $(PG_USER) -d $(PG_DB)

redis-cli:  ## open redis-cli
	@docker exec -it $(REDIS_CONTAINER) redis-cli

ollama-list:  ## list ollama models
	@docker exec $(OLLAMA_CONTAINER) ollama list

test-extensions:  ## pgvector smoke test
	@bash scripts/test_extensions.sh

test-postgres:  ## comprehensive PG smoke test
	@bash scripts/test_postgres.sh

test:  ## full pytest suite
	@$(PYTHON) -m pytest -q

init-db:  ## create schema + extensions; idempotent
	@$(PYTHON) -m cfp init-db

seeds:  ## enqueue seed URLs from prompts.md
	@$(PYTHON) -m cfp enqueue-seeds

run:  ## run the 4-tier pipeline
	@$(PYTHON) -m cfp run-pipeline

reports:  ## regenerate markdown reports
	@$(PYTHON) -m cfp generate-reports

doctor:  ## end-to-end health check
	@$(PYTHON) -m cfp doctor

sync-pull:  ## (v2) restore latest pg_dump from GCS
	@$(PYTHON) -m cfp sync-pull

sync-push:  ## (v2) pg_dump + push to GCS
	@$(PYTHON) -m cfp sync-push

models:  ## pull Ollama models for $(PROFILE)
	@CFP_MACHINE=$(PROFILE) bash setup.sh --models-only
```

---

## scripts/test_postgres.sh (new)

Comprehensive PG smoke test — extension list, schema tables exist
(after init-db), required indexes, connection pool size, listen/notify
roundtrip. Goes deeper than `test_extensions.sh` (which is pgvector-focused).

```bash
#!/usr/bin/env bash
set -e
PSQL="docker exec -i cfp_postgres psql -U cfp -d cfp -v ON_ERROR_STOP=1"

echo "=== PostgreSQL comprehensive smoke test ==="

# Extensions
$PSQL -c "SELECT extname, extversion FROM pg_extension ORDER BY extname;"

# Connection
$PSQL -tAc "SELECT current_user || '@' || current_database();"

# Required tables (after init-db)
TABLES=(events series venues people event_people event_organisations
        event_embeddings sites tier_runs scrape_queue scrape_sessions)
for t in "${TABLES[@]}"; do
    if $PSQL -tAc "SELECT 1 FROM information_schema.tables WHERE table_name='$t'" | grep -q 1; then
        echo "  ✓ $t exists"
    else
        echo "  [skip] $t missing (run init-db first)"
    fi
done

# Pool / max_connections
$PSQL -tAc "SHOW max_connections;"

# Listen/Notify roundtrip
$PSQL -c "LISTEN cfp_test;" >/dev/null 2>&1 || true

# Transaction rollback
$PSQL <<'SQL'
BEGIN;
CREATE TABLE _rollback_test (n int);
INSERT INTO _rollback_test VALUES (1);
ROLLBACK;
SQL
$PSQL -tAc "SELECT 1 FROM information_schema.tables WHERE table_name='_rollback_test';" | grep -q . \
    && echo "  ✗ rollback failed" || echo "  ✓ transaction rollback works"

echo "=== PostgreSQL smoke tests passed ==="
```

---

## Tests (tests/test_makefile.py + tests/test_compose.py)

```python
# test_makefile.py
import subprocess, re
from pathlib import Path

REQUIRED = {"help","up","down","wipe","ps","logs","psql","redis-cli",
            "ollama-list","test-extensions","test-postgres","test",
            "init-db","seeds","run","reports","doctor",
            "sync-pull","sync-push","models"}

def test_makefile_targets_present():
    text = Path("Makefile").read_text()
    found = set(re.findall(r"^([a-zA-Z][a-zA-Z0-9_-]*):.*##", text, re.M))
    assert not (REQUIRED - found)

def test_default_goal_is_help():
    assert ".DEFAULT_GOAL := help" in Path("Makefile").read_text()

def test_wipe_requires_confirmation():
    text = Path("Makefile").read_text()
    assert "read -r -p" in text
    assert 'if [ "$$ans" = "wipe" ]' in text
```

```python
# test_compose.py
import yaml
from pathlib import Path

def test_compose_v1_invariants():
    spec = yaml.safe_load(Path("docker-compose.yml").read_text())
    s = spec["services"]
    assert s["postgres"]["image"] == "pgvector/pgvector:pg16"
    assert s["redis"]["image"] == "redis:7-alpine"
    assert s["ollama"]["image"] == "ollama/ollama"
    env = s["postgres"]["environment"]
    assert env["POSTGRES_USER"] == "cfp"
    assert env["POSTGRES_DB"] == "cfp"
    assert any("/mnt/d/wsl/ollama:/root/.ollama" in v for v in s["ollama"]["volumes"])
    devs = s["ollama"]["deploy"]["resources"]["reservations"]["devices"]
    assert any(d["driver"] == "nvidia" for d in devs)
```

---

## Acceptance Criteria

- `make up && make doctor` from a fresh clone (after `setup.sh` + models)
  reports green for PG, Redis, Ollama, prompts.md parse.
- `make test` runs full pytest suite against running stack.
- `make wipe` requires literal `wipe` confirmation; Ollama models survive.
- `make help` is default target; lists every target with description.
- Every operational action reachable via Make — no developer should type
  `docker compose ...` or `python -m cfp ...` directly.

---

## Downstream Consumers

| Consumer | Usage |
|---|---|
| `setup.sh` | calls `docker compose up -d` during bootstrap; `make models` for re-pulls |
| `cfp/cli.py` | targets are 1-to-1 with CLI verbs |
| `arch.md §6` lifecycle | `make up; make seeds; make run; make reports` |
| `README.md` Quick Start | reduce to `bash setup.sh && make up && make doctor && make seeds && make run` |

Only file that hardcodes container names — keep in sync with `setup.sh` and `scripts/test_extensions.sh`.
