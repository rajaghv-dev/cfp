#!/usr/bin/env bash
# Smoke tests for PostgreSQL extensions used by the cfp pipeline.
# Run: bash scripts/test_extensions.sh
#
# v1 only requires `vector` (pgvector). v2 will add `age` (Apache AGE) when
# the docker image is switched from pgvector/pgvector:pg16 to apache/age:PG16_latest.
set -e

PSQL="docker exec -i cfp_postgres psql -U cfp -d cfp -v ON_ERROR_STOP=1"

green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
red()   { printf '\033[0;31m%s\033[0m\n' "$1"; }
yellow(){ printf '\033[0;33m%s\033[0m\n' "$1"; }

echo "================================================================"
echo "PostgreSQL extension smoke tests"
echo "================================================================"

# ---------------------------------------------------------------------
# Test 1: pgvector (REQUIRED — v1)
# ---------------------------------------------------------------------
yellow "[1/4] pgvector — CREATE EXTENSION"
$PSQL -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null
$PSQL -tAc "SELECT extversion FROM pg_extension WHERE extname='vector';"
green "  ✓ vector extension installed"

yellow "[2/4] pgvector — CREATE TABLE + INSERT + cosine similarity"
$PSQL <<'SQL' >/dev/null
DROP TABLE IF EXISTS _test_vectors;
CREATE TABLE _test_vectors (
    id   serial PRIMARY KEY,
    name text,
    vec  vector(768)
);
INSERT INTO _test_vectors (name, vec)
SELECT 'doc-' || g, ARRAY(SELECT random() FROM generate_series(1,768))::vector
FROM generate_series(1,1000) g;
SQL
ROW_COUNT=$($PSQL -tAc "SELECT count(*) FROM _test_vectors;")
[ "$ROW_COUNT" = "1000" ] && green "  ✓ inserted 1000 vectors of dim 768" || { red "  ✗ inserted $ROW_COUNT, expected 1000"; exit 1; }

# Cosine similarity query (the actual operation pipeline uses for dedup)
$PSQL <<'SQL' >/dev/null
SELECT id, name, 1 - (vec <=> (SELECT vec FROM _test_vectors WHERE id=1)) AS cosine_sim
FROM _test_vectors
ORDER BY vec <=> (SELECT vec FROM _test_vectors WHERE id=1)
LIMIT 5;
SQL
green "  ✓ cosine similarity (vec <=> vec) works"

yellow "[3/4] pgvector — IVFFlat index (the v1 dedup index per arch.md §1 Q8)"
$PSQL <<'SQL' >/dev/null
CREATE INDEX _test_ivf ON _test_vectors USING ivfflat (vec vector_cosine_ops)
  WITH (lists = 32);
ANALYZE _test_vectors;
SQL
INDEX_CHECK=$($PSQL -tAc "SELECT indexname FROM pg_indexes WHERE tablename='_test_vectors' AND indexname='_test_ivf';")
[ "$INDEX_CHECK" = "_test_ivf" ] && green "  ✓ IVFFlat index built (lists=32)" || { red "  ✗ IVFFlat index missing"; exit 1; }

yellow "[4/4] pgvector — HNSW index (v2 fallback for higher recall — arch.md §1 Q8)"
$PSQL <<'SQL' >/dev/null
CREATE INDEX _test_hnsw ON _test_vectors USING hnsw (vec vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
SQL
HNSW_CHECK=$($PSQL -tAc "SELECT indexname FROM pg_indexes WHERE tablename='_test_vectors' AND indexname='_test_hnsw';")
[ "$HNSW_CHECK" = "_test_hnsw" ] && green "  ✓ HNSW index built (m=16, ef_construction=64)" || { red "  ✗ HNSW index missing"; exit 1; }

# Cleanup
$PSQL -c "DROP TABLE _test_vectors CASCADE;" >/dev/null

# ---------------------------------------------------------------------
# Skipped (not in v1 image): Apache AGE
# ---------------------------------------------------------------------
echo ""
yellow "[skip] Apache AGE — not in pgvector/pgvector:pg16 image (v1)"
yellow "       Available in v2 when image switches to apache/age:PG16_latest."
AGE_AVAILABLE=$($PSQL -tAc "SELECT count(*) FROM pg_available_extensions WHERE name='age';")
[ "$AGE_AVAILABLE" = "0" ] && green "  ✓ correctly absent for v1" || red "  ✗ age unexpectedly present"

# ---------------------------------------------------------------------
# Connection sanity
# ---------------------------------------------------------------------
yellow "[bonus] Connection sanity check"
$PSQL -tAc "SELECT current_user || '@' || current_database();"
green "  ✓ connected as cfp@cfp"

echo ""
green "================================================================"
green "All extension tests passed."
green "================================================================"
