#!/usr/bin/env bash
# Create/update the bucket and the dedicated user DarkAngel keeps home files in,
# on the Infra MinIO -- DarkAngel's twin of Infra's scripts/provision-ea-minio.sh.
# Idempotent: safe to re-run, e.g. to rotate MINIO_SECRET_KEY (then `make up`, so
# the API is handed the new one).
#
#   bucket  darkangel-files   versioned: an overwritten or deleted file can be
#                             restored from the MinIO console
#   user    darkangel-api     read/write on that bucket only (darkangel-files-rw)
#
# mc runs in a throwaway container of the Infra's own MinIO image, on infra-net,
# so this needs the Docker host that runs the Infra stack but no mc install. The
# secrets reach it on stdin, never as command-line arguments.
#
# Root credentials: MINIO_ROOT_USER / MINIO_ROOT_PASSWORD from the environment,
# else the Infra .env (INFRA_ENV, default ../Infra/.env). The user's secret:
# MINIO_SECRET_KEY from the environment, else .portainer.env.
#
# Usage: scripts/provision-minio.sh
set -euo pipefail
cd "$(dirname "$0")/.."

INFRA_ENV="${INFRA_ENV:-../Infra/.env}"
MINIO_NETWORK="${MINIO_NETWORK:-infra-net}"
# The release the Infra stack pins for its minio service; it ships mc and bash.
MINIO_IMAGE="${MINIO_IMAGE:-quay.io/minio/minio:RELEASE.2025-09-07T16-13-09Z}"

die() { printf 'provision-minio.sh: %b\n' "$*" >&2; exit 1; }

env_value() { # <key> <file>: last value of KEY= in file, unquoted
  sed -n "s/^$1=//p" "$2" 2>/dev/null | tail -1 | sed "s/^[\"']//; s/[\"']\$//"
}
MINIO_ROOT_USER="${MINIO_ROOT_USER:-$(env_value MINIO_ROOT_USER "$INFRA_ENV")}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-$(env_value MINIO_ROOT_PASSWORD "$INFRA_ENV")}"
MINIO_SECRET_KEY="${MINIO_SECRET_KEY:-$(env_value MINIO_SECRET_KEY .portainer.env)}"
[ -n "$MINIO_ROOT_USER" ] && [ -n "$MINIO_ROOT_PASSWORD" ] ||
  die "set MINIO_ROOT_USER and MINIO_ROOT_PASSWORD, or INFRA_ENV to the Infra .env"
[ "${#MINIO_SECRET_KEY}" -ge 8 ] && [ "$MINIO_SECRET_KEY" != change-me ] ||
  die "set MINIO_SECRET_KEY (8+ characters) in .portainer.env"

# MSYS_*: keep Git for Windows from rewriting arguments (see portainer-stack.sh).
printf '%s\n%s\n%s\n' "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" "$MINIO_SECRET_KEY" |
  MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
  docker run --rm -i --network "$MINIO_NETWORK" --entrypoint bash "$MINIO_IMAGE" \
  -euo pipefail -c '
IFS= read -r root_user; IFS= read -r root_pass; IFS= read -r app_secret
export MC_CONFIG_DIR="$(mktemp -d)"

mc alias set infra http://minio:9000 "$root_user" "$root_pass" >/dev/null
mc mb --ignore-existing infra/darkangel-files
mc version enable infra/darkangel-files >/dev/null

# GetBucketLocation: minio-py asks for the bucket region before its first
# call on it, so without this every request is AccessDenied. The tests run as
# root, which no policy binds, so only a real deploy shows it.
cat >"$MC_CONFIG_DIR/policy.json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
      "Resource": ["arn:aws:s3:::darkangel-files"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": ["arn:aws:s3:::darkangel-files/*"]
    }
  ]
}
JSON
mc admin policy create infra darkangel-files-rw "$MC_CONFIG_DIR/policy.json" >/dev/null

# Creates the user, or resets its secret if it already exists.
mc admin user add infra darkangel-api "$app_secret" >/dev/null

if ! out="$(mc admin policy attach infra darkangel-files-rw --user darkangel-api 2>&1)"; then
  case "$out" in
    *"already in effect"*) ;;
    *) echo "$out" >&2; exit 1 ;;
  esac
fi

echo "provision-minio.sh: bucket darkangel-files + user darkangel-api (policy darkangel-files-rw) ready"
'
