#!/usr/bin/env bash
# Run with: sudo bash scripts/setup_postgres.sh
# Installs PostgreSQL 16 + pgvector natively on Ubuntu 24.04 (WSL2)
# PG_DSN=postgresql://cfp:cfp@localhost:5432/cfp
set -e

echo "=== Installing PostgreSQL 16 + pgvector ==="
apt-get update -qq
apt-get install -y postgresql postgresql-16-pgvector

echo "=== Starting PostgreSQL ==="
service postgresql start

echo "=== Creating user, database, extensions ==="
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='cfp'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE USER cfp WITH PASSWORD 'cfp';"

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='cfp'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE cfp OWNER cfp;"

sudo -u postgres psql -d cfp -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d cfp -c "GRANT ALL PRIVILEGES ON DATABASE cfp TO cfp;"
sudo -u postgres psql -d cfp -c "GRANT ALL ON SCHEMA public TO cfp;"

echo "=== Verifying pgvector ==="
sudo -u postgres psql -d cfp -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"

echo ""
echo "=== Done ==="
echo "PG_DSN=postgresql://cfp:cfp@localhost:5432/cfp"
echo ""
echo "To start PostgreSQL on next WSL2 launch: sudo service postgresql start"
echo "Or add to ~/.bashrc: sudo service postgresql start 2>/dev/null"
