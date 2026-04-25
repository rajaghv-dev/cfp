#!/usr/bin/env bash
set -euo pipefail

echo "=== WikiCFP Conference Scraper — Setup ==="

# 1. Python check
if ! command -v python3 &>/dev/null; then
  echo "Error: python3 not found. Install Python 3.10+ and re-run."
  exit 1
fi
PY=$(python3 --version)
echo "Python: $PY"

# 2. Virtual environment
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

# Activate
# shellcheck disable=SC1091
source .venv/bin/activate

# 3. Dependencies
echo "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "Dependencies installed."

# 4. Data directory
mkdir -p data
echo "Data directory ready: ./data"

# 5. Quick connectivity check
echo "Testing WikiCFP connectivity..."
if python3 -c "
import requests, sys
try:
    r = requests.get('http://www.wikicfp.com', timeout=8)
    sys.exit(0 if r.status_code == 200 else 1)
except Exception as e:
    print('  Warning:', e)
    sys.exit(1)
"; then
  echo "WikiCFP reachable."
else
  echo "Warning: could not reach wikicfp.com — scrape may fail."
fi

# 6. Update README with last-setup timestamp
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
if [ -f README.md ]; then
  # Replace or append the last-setup line
  if grep -q "^Last setup:" README.md; then
    sed -i "s/^Last setup:.*/Last setup: $TIMESTAMP/" README.md
  else
    echo "" >> README.md
    echo "Last setup: $TIMESTAMP" >> README.md
  fi
  echo "README.md updated."
fi

echo ""
echo "Setup complete. Run the scraper:"
echo "  source .venv/bin/activate"
echo "  python3 scraper.py --pages 3"
echo ""
echo "Output is written to ./data/latest.json and ./data/latest.csv (symlink)"
