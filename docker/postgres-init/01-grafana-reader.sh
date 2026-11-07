#!/bin/sh
# Runs once, when the PostgreSQL volume is created.
# Creates a read-only role for Grafana: it can read every table the ingestion
# script creates (now or later), and cannot modify or drop anything.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v reader_password="$GRAFANA_DB_PASSWORD" -v db="$POSTGRES_DB" -v owner="$POSTGRES_USER" <<'SQL'
CREATE ROLE grafana_reader LOGIN PASSWORD :'reader_password';
GRANT CONNECT ON DATABASE :"db" TO grafana_reader;
GRANT USAGE ON SCHEMA public TO grafana_reader;
-- The ingestion script drops and recreates its tables on every run,
-- so the grant must also cover tables created in the future.
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public GRANT SELECT ON TABLES TO grafana_reader;
SQL
