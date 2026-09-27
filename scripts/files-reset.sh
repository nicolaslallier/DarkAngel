#!/usr/bin/env bash
# One-time cutover tool for the MinIO -> SeaweedFS move (see
# docs/superpowers/specs/2026-09-27-minio-to-seaweedfs-design.md, section 6).
# Infra started SeaweedFS empty, so every files/file_versions row points at
# bytes that no longer exist. This empties both tables. folders (no bytes) and
# audit_log (history, no foreign key) stay. Delete this script after cutover.
#
#   PGADMIN_URL=postgresql://... make files-reset                 # show counts
#   PGADMIN_URL=postgresql://... CONFIRM=darkangel make files-reset
set -euo pipefail

: "${PGADMIN_URL:?set PGADMIN_URL to an admin connection string for the Infra Postgres}"
DB="${DB:-darkangel}"

q() { psql "$PGADMIN_URL" --quiet --no-psqlrc --set ON_ERROR_STOP=1 -tA -c "\\connect $DB" -c "$1"; }

counts="$(q "SELECT (SELECT count(*) FROM files) || ' files, '
               || (SELECT count(*) FROM file_versions) || ' versions'")"
echo "files-reset.sh: $DB holds $counts"

[ "${CONFIRM:-}" = "$DB" ] ||
  { echo "files-reset.sh: nothing dropped; rerun with CONFIRM=$DB to empty them" >&2; exit 1; }

q "TRUNCATE files, file_versions"
echo "files-reset.sh: emptied files and file_versions in $DB"
