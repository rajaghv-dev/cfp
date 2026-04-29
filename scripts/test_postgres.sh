#!/usr/bin/env bash
# Comprehensive PostgreSQL smoke test.
# Run: bash scripts/test_postgres.sh
# Requires cfp_postgres container running with pgvector loaded.
set -e

PSQL="docker exec -i cfp_postgres psql -U cfp -d cfp -v ON_ERROR_STOP=1"

green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
red()   { printf '\033[0;31m%s\033[0m\n' "$1"; }
yellow(){ printf '\033[0;33m%s\033[0m\n' "$1"; }

echo "================================================================"
echo "PostgreSQL comprehensive smoke test"
echo "================================================================"

# Connection
yellow "[1] Connection sanity"
$PSQL -tAc "SELECT current_user || '@' || current_database();" | tee /dev/null
green "  ✓ connected"

# Extensions
yellow "[2] Extensions installed"
$PSQL -c "SELECT extname, extversion FROM pg_extension ORDER BY extname;"

# Required tables (only after init-db has been run; otherwise skip)
yellow "[3] Required tables (post-init-db)"
TABLES=(events series venues people event_people person_affiliations
        event_organisations organisations event_embeddings sites
        tier_runs scrape_queue scrape_sessions)
PRESENT=0
for t in "${TABLES[@]}"; do
    if $PSQL -tAc "SELECT 1 FROM information_schema.tables WHERE table_name='$t'" 2>/dev/null | grep -q 1; then
        PRESENT=$((PRESENT + 1))
    fi
done
echo "  $PRESENT/${#TABLES[@]} tables present"
[ "$PRESENT" = "${#TABLES[@]}" ] && green "  ✓ schema initialised" || yellow "  [info] run 'make init-db' to create schema"

# Connection pool
yellow "[4] max_connections setting"
$PSQL -tAc "SHOW max_connections;"

# Transaction rollback
yellow "[5] Transaction rollback"
$PSQL <<'SQL' >/dev/null 2>&1
BEGIN;
CREATE TABLE _rollback_test (n int);
INSERT INTO _rollback_test VALUES (1);
ROLLBACK;
SQL
if $PSQL -tAc "SELECT 1 FROM information_schema.tables WHERE table_name='_rollback_test';" 2>/dev/null | grep -q 1; then
    red "  ✗ rollback failed — _rollback_test still exists"
    exit 1
else
    green "  ✓ transaction rollback works"
fi

# Concurrent connections
yellow "[6] Concurrent connection counts"
$PSQL -tAc "SELECT count(*) FROM pg_stat_activity WHERE datname='cfp';"

green "================================================================"
green "PostgreSQL smoke tests passed."
green "================================================================"
