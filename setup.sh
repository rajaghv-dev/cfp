#!/usr/bin/env bash
# setup.sh — bring up the cfp pipeline on a fresh machine.
# Usage:
#   bash setup.sh                                # set up current directory
#   bash setup.sh <repo-url>                     # clone first, then set up
#   CFP_MACHINE=gpu_large bash setup.sh         # set hardware profile
#
# CFP_MACHINE values: cpu_only | gpu_small | gpu_mid | gpu_large | dgx
#
# Order of operations:
#   1. Clone / cd into repo
#   2. Bring up Docker stack (postgres + pgvector, redis, ollama)
#   3. Wait for postgres healthcheck
#   4. Validate extensions via scripts/test_extensions.sh
#   5. Python venv + dependencies
#   6. Pull Ollama models for the CFP_MACHINE profile
#   7. Connectivity check (WikiCFP)
set -euo pipefail

REPO_URL="${1:-}"
CFP_MACHINE="${CFP_MACHINE:-gpu_mid}"
PYTHON="${PYTHON:-python3}"
VENV=".venv"

green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
red()   { printf '\033[0;31m%s\033[0m\n' "$1"; }
yellow(){ printf '\033[0;33m%s\033[0m\n' "$1"; }

# ── 1. Clone or cd into repo ──────────────────────────────────────────────
if [ -n "$REPO_URL" ]; then
    REPO_DIR="${2:-$(basename "$REPO_URL" .git)}"
    if [ -d "$REPO_DIR/.git" ]; then
        echo "Updating existing repo: $REPO_DIR"
        git -C "$REPO_DIR" pull --ff-only
    else
        echo "Cloning $REPO_URL → $REPO_DIR"
        git clone "$REPO_URL" "$REPO_DIR"
    fi
    cd "$REPO_DIR"
fi

echo "================================================================"
echo "CFP setup — machine profile: $CFP_MACHINE"
echo "Working directory: $(pwd)"
echo "================================================================"

# ── 2. Docker stack: enable + connect + validate ──────────────────────────
yellow "[step 1/7] Docker stack — enable + connect + validate"

if ! command -v docker &>/dev/null; then
    red "  ✗ docker not found — install Docker Desktop (Windows/Mac) or docker-ce (Linux)"
    red "    On WSL2 also enable: Docker Desktop → Settings → Resources → WSL Integration"
    exit 1
fi

# Force Unix-socket context on WSL2 (avoids the desktop-linux npipe error).
if [ -S /var/run/docker.sock ]; then
    export DOCKER_CONTEXT=default
fi

if ! docker info >/dev/null 2>&1; then
    red "  ✗ docker daemon not reachable. Try:"
    red "    1) ensure Docker Desktop is running"
    red "    2) WSL2: docker context use default"
    exit 1
fi
green "  ✓ docker reachable ($(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown))"

if [ ! -f docker-compose.yml ]; then
    red "  ✗ docker-compose.yml not found in $(pwd)"
    exit 1
fi

echo "  Bringing up postgres + redis + ollama (docker compose up -d)..."
docker compose up -d >/dev/null
green "  ✓ containers started"

echo "  Waiting for postgres to be healthy..."
for i in $(seq 1 30); do
    STATUS=$(docker inspect --format '{{.State.Health.Status}}' cfp_postgres 2>/dev/null || echo "starting")
    if [ "$STATUS" = "healthy" ]; then
        green "  ✓ cfp_postgres healthy after ${i}s"
        break
    fi
    if [ "$i" -eq 30 ]; then
        red "  ✗ cfp_postgres did not become healthy in 30s"
        docker compose logs postgres | tail -20
        exit 1
    fi
    sleep 1
done

# Run the extension smoke test
if [ -x scripts/test_extensions.sh ]; then
    echo "  Running scripts/test_extensions.sh..."
    if bash scripts/test_extensions.sh >/tmp/cfp_ext_test.log 2>&1; then
        green "  ✓ pgvector smoke test passed"
    else
        red "  ✗ pgvector smoke test failed — see /tmp/cfp_ext_test.log"
        tail -30 /tmp/cfp_ext_test.log
        exit 1
    fi
else
    yellow "  [warn] scripts/test_extensions.sh missing — skipping validation"
fi

# Redis ping
if docker exec cfp_redis redis-cli ping 2>/dev/null | grep -q PONG; then
    green "  ✓ cfp_redis responsive"
else
    red "  ✗ cfp_redis not responding to PING"
    exit 1
fi

# Ollama health
if docker exec cfp_ollama ollama list >/dev/null 2>&1; then
    green "  ✓ cfp_ollama responsive"
else
    yellow "  [warn] cfp_ollama not yet responsive — first start may take a few seconds"
fi

# ── 3. Python ─────────────────────────────────────────────────────────────
yellow "[step 2/7] Python check"
if ! command -v "$PYTHON" &>/dev/null; then
    red "  ✗ python3 not found. Install Python 3.10+ and re-run."
    exit 1
fi
PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
    green "  ✓ Python $PY_VERSION"
else
    red "  ✗ Python 3.10+ required (found $PY_VERSION)"
    exit 1
fi

# ── 4. Virtual environment ────────────────────────────────────────────────
yellow "[step 3/7] Virtual environment"
if [ ! -d "$VENV" ]; then
    "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
green "  ✓ venv: $(which python3)"

# ── 5. Dependencies ───────────────────────────────────────────────────────
yellow "[step 4/7] Python dependencies"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
green "  ✓ requirements.txt installed"

# ── 6. Data directories ───────────────────────────────────────────────────
yellow "[step 5/7] Data directories"
mkdir -p data/archive data/pg_backup reports ontology notes
green "  ✓ data/archive data/pg_backup reports ontology notes"

# ── 7. Ollama models for this profile ─────────────────────────────────────
# Mirrors PROFILE_MODELS in codegen/01_config_models.md (Q14 RESOLVED — pinned quant tags).
yellow "[step 6/7] Ollama models for profile: $CFP_MACHINE"

declare -A PROFILE_MODELS
PROFILE_MODELS[cpu_only]="qwen3:4b-q4_K_M nomic-embed-text"
PROFILE_MODELS[gpu_small]="qwen3:4b-q4_K_M nomic-embed-text"
PROFILE_MODELS[gpu_mid]="qwen3:4b-q4_K_M qwen3:14b-q4_K_M nomic-embed-text"
PROFILE_MODELS[gpu_large]="qwen3:4b-q4_K_M qwen3:14b-q4_K_M qwen3:32b-q4_K_M deepseek-r1:32b nomic-embed-text"
PROFILE_MODELS[dgx]="qwen3:4b-q8_0 qwen3:14b-q8_0 qwen3:32b-q8_0 deepseek-r1:32b deepseek-r1:70b nomic-embed-text"

MODELS="${PROFILE_MODELS[$CFP_MACHINE]:-}"
if [ -z "$MODELS" ]; then
    yellow "  [warn] unknown CFP_MACHINE=$CFP_MACHINE — skipping model pull"
    yellow "         valid: cpu_only | gpu_small | gpu_mid | gpu_large | dgx"
else
    for m in $MODELS; do
        if docker exec cfp_ollama ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$m"; then
            green "  ✓ $m already pulled"
        else
            echo "  Pulling $m ..."
            if docker exec cfp_ollama ollama pull "$m" >/dev/null 2>&1; then
                green "  ✓ $m pulled"
            else
                yellow "  [warn] failed to pull $m — re-run later: docker exec cfp_ollama ollama pull $m"
            fi
        fi
    done
fi

# ── 8. WikiCFP reachability (informational) ───────────────────────────────
yellow "[step 7/7] WikiCFP reachability"
if python3 -c "
import urllib.request, sys
try:
    req = urllib.request.Request('http://www.wikicfp.com', headers={'User-Agent': 'cfp-setup/1.0'})
    urllib.request.urlopen(req, timeout=8).close()
    sys.exit(0)
except Exception as e:
    sys.exit(1)
" 2>/dev/null; then
    green "  ✓ WikiCFP reachable"
else
    yellow "  [warn] WikiCFP unreachable — scrape will fail until network is available"
fi

# ── 9. Update README + SESSION timestamps ────────────────────────────────
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
for f in README.md SESSION.md; do
    [ -f "$f" ] || continue
    if grep -q "^Last setup:" "$f"; then
        sed -i "s/^Last setup:.*/Last setup: $TIMESTAMP (machine: $CFP_MACHINE)/" "$f"
    elif [ "$f" = "README.md" ]; then
        printf '\nLast setup: %s (machine: %s)\n' "$TIMESTAMP" "$CFP_MACHINE" >> "$f"
    fi
done

echo ""
green "================================================================"
green "Setup complete — machine: $CFP_MACHINE"
green "================================================================"
echo ""
echo "Next:"
echo "  source $VENV/bin/activate"
echo "  python3 scraper.py --pages 2     # standalone v1 scraper"
echo ""
echo "Once cfp/ package lands:"
echo "  python3 -m cfp init-db"
echo "  python3 -m cfp enqueue-seeds"
echo "  python3 -m cfp run-pipeline"
echo ""
echo "Stack control:"
echo "  docker compose ps           # status"
echo "  docker compose logs -f      # tail logs"
echo "  docker compose down         # stop stack (volumes preserved)"
