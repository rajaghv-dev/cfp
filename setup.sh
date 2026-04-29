#!/usr/bin/env bash
# setup.sh — clone or update the cfp repo and set up Python environment
# Usage:
#   bash setup.sh                        # set up current directory
#   bash setup.sh <repo-url>             # clone from URL first, then set up
#   CFP_MACHINE=rtx4090 bash setup.sh   # set machine role (rtx3080|rtx4090|dgx|local)
set -euo pipefail

REPO_URL="${1:-}"
CFP_MACHINE="${CFP_MACHINE:-local}"
PYTHON="${PYTHON:-python3}"
VENV=".venv"

# ── 1. Clone or update repo ────────────────────────────────────────────────
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

echo "=== CFP Setup (machine: $CFP_MACHINE) ==="
echo "Working directory: $(pwd)"

# ── 2. Python check ────────────────────────────────────────────────────────
if ! command -v "$PYTHON" &>/dev/null; then
    echo "Error: python3 not found. Install Python 3.10+ and re-run."
    exit 1
fi
PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python: $PY_VERSION"
if "$PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
    echo "  ✓ Python version OK"
else
    echo "  ✗ Python 3.10+ required (found $PY_VERSION)"
    exit 1
fi

# ── 3. Virtual environment ─────────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment: $VENV"
    "$PYTHON" -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"
echo "  ✓ venv activated: $(which python3)"

# ── 4. Dependencies ────────────────────────────────────────────────────────
echo "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "  ✓ Dependencies installed"

# ── 5. Data directory ─────────────────────────────────────────────────────
mkdir -p data/archive data/pg_backup reports ontology notes
echo "  ✓ Directories ready"

# ── 6. Ollama models (optional — skipped if Ollama not running) ───────────
declare -A MACHINE_MODELS
MACHINE_MODELS[rtx3080]="qwen3:4b qwen3:14b mistral-nemo:12b nomic-embed-text"
MACHINE_MODELS[rtx4090]="qwen3:32b deepseek-r1:32b"
MACHINE_MODELS[dgx]="deepseek-r1:70b"
MACHINE_MODELS[local]="qwen3:4b nomic-embed-text"

MODELS_FOR_MACHINE="${MACHINE_MODELS[$CFP_MACHINE]:-}"

if [ -n "$MODELS_FOR_MACHINE" ] && command -v ollama &>/dev/null; then
    echo "Checking Ollama models for $CFP_MACHINE..."
    for model in $MODELS_FOR_MACHINE; do
        if ollama list 2>/dev/null | grep -q "^${model}"; then
            echo "  ✓ $model already pulled"
        else
            echo "  Pulling $model ..."
            ollama pull "$model" || echo "  [warn] failed to pull $model"
        fi
    done
else
    echo "  Ollama not found or CFP_MACHINE=local — skipping model pull"
    echo "  To pull models manually: ollama pull qwen3:4b"
fi

# ── 7. Connectivity checks (optional) ─────────────────────────────────────
echo "Checking connectivity..."

# WikiCFP
if python3 -c "
import requests, sys
try:
    r = requests.get('http://www.wikicfp.com', timeout=8, headers={'User-Agent': 'cfp-setup/1.0'})
    sys.exit(0 if r.status_code == 200 else 1)
except Exception as e:
    print(f'  [warn] wikicfp.com: {e}')
    sys.exit(1)
" 2>/dev/null; then
    echo "  ✓ WikiCFP reachable"
else
    echo "  [warn] WikiCFP unreachable — scrape will fail"
fi

# PostgreSQL (optional)
if python3 -c "
import psycopg, os, sys
try:
    dsn = os.getenv('PG_DSN', 'postgresql://cfp:cfp@localhost:5432/cfp')
    psycopg.connect(dsn, connect_timeout=3).close()
    sys.exit(0)
except Exception as e:
    print(f'  [info] PostgreSQL not available: {e}')
    sys.exit(1)
" 2>/dev/null; then
    echo "  ✓ PostgreSQL reachable"
else
    echo "  [info] PostgreSQL not running — run: docker compose up -d"
fi

# ── 8. Update README with setup timestamp ─────────────────────────────────
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
if [ -f README.md ]; then
    if grep -q "^Last setup:" README.md; then
        sed -i "s/^Last setup:.*/Last setup: $TIMESTAMP (machine: $CFP_MACHINE)/" README.md
    else
        printf '\nLast setup: %s (machine: %s)\n' "$TIMESTAMP" "$CFP_MACHINE" >> README.md
    fi
fi
if [ -f SESSION.md ]; then
    if grep -q "^Last setup:" SESSION.md; then
        sed -i "s/^Last setup:.*/Last setup: $TIMESTAMP (machine: $CFP_MACHINE)/" SESSION.md
    fi
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  source $VENV/bin/activate"
echo ""
echo "  # Quick scrape (existing implementation):"
echo "  python3 scraper.py --pages 2"
echo ""
echo "  # Once cfp/ package is implemented:"
echo "  docker compose up -d"
echo "  python3 -m cfp init-db"
echo "  python3 -m cfp enqueue-seeds"
echo "  python3 -m cfp run-pipeline"
echo ""
echo "  See SESSION.md for full context and next steps."
