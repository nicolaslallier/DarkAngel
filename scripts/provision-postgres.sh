#!/usr/bin/env bash
# Create/update the DarkAngel database and its scoped role in the Infra
# PostgreSQL. Idempotent: safe to re-run, and re-running resets the password.
#
#   database  darkangel
#   role      darkangel  -- owns that database and nothing else
#
# Needs an admin connection to the Infra Postgres in PGADMIN_URL, and writes
# the generated password back into .portainer.env as POSTGRES_PASSWORD, the
# same way provision-minio.sh handles MINIO_SECRET_KEY.
set -euo pipefail
cd "$(dirname "$0")/.."

: "${PGADMIN_URL:?set PGADMIN_URL to an admin connection string for the Infra Postgres}"
ENV_FILE="${ENV_FILE:-.portainer.env}"

password="$(openssl rand -base64 30 | tr -d '/+=' | head -c 32)"

psql "$PGADMIN_URL" --quiet --no-psqlrc --set ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'darkangel') THEN
    CREATE ROLE darkangel LOGIN PASSWORD '${password}';
  ELSE
    ALTER ROLE darkangel LOGIN PASSWORD '${password}';
  END IF;
END
\$\$;
SQL

# CREATE DATABASE cannot run inside the DO block above.
if ! psql "$PGADMIN_URL" -tAc "SELECT 1 FROM pg_database WHERE datname='darkangel'" | grep -q 1; then
  psql "$PGADMIN_URL" --quiet --set ON_ERROR_STOP=1 \
    -c "CREATE DATABASE darkangel OWNER darkangel"
fi

if grep -q '^POSTGRES_PASSWORD=' "$ENV_FILE" 2>/dev/null; then
  # BSD and GNU sed disagree on -i; rewrite through a temp file instead.
  tmp="$(mktemp)"
  sed "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${password}|" "$ENV_FILE" >"$tmp"
  mv "$tmp" "$ENV_FILE"
else
  echo "POSTGRES_PASSWORD=${password}" >>"$ENV_FILE"
fi

echo "provision-postgres.sh: database darkangel + role darkangel ready; password written to $ENV_FILE"
