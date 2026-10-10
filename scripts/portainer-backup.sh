#!/usr/bin/env bash
# List the Portainer instances in .portainers.env and back each one up to the
# Infra SeaweedFS, using Portainer's native backup (POST /api/backup), which
# Portainer encrypts with PORTAINER_BACKUP_PASSWORD.
#
# Usage: scripts/portainer-backup.sh list|backup|selftest
#   list     name, URL, version, environments, stacks and reachability per instance
#   backup   one encrypted archive per instance -> s3://<bucket>/<instance>/<UTC stamp>.tar.gz.encrypted
#   selftest check the pure helpers without touching Portainer or S3
set -euo pipefail
cd "$(dirname "$0")/.."

AWS_CLI_IMAGE="${AWS_CLI_IMAGE:-amazon/aws-cli:2.17.30}"

die() { printf 'portainer-backup.sh: %b\n' "$*" >&2; exit 1; }
note() { printf 'portainer-backup.sh: %b\n' "$*"; }

is_set() { [ -n "${1:-}" ] && [ "$1" != change-me ]; }
valid_name() { case "$1" in ''|*[!A-Z0-9_]*) return 1 ;; *) return 0 ;; esac; }
slug() { printf '%s' "$1" | tr 'A-Z_' 'a-z-'; }
backup_key() { printf '%s/%s.tar.gz.encrypted' "$(slug "$1")" "$2"; } # <NAME> <timestamp>
# The password goes to jq through its environment, not --arg: jq is an external
# process, so an argument would show up in `ps`.
backup_body() { P="$1" jq -n '{password: env.P}'; }
inst_var() { local v="PORTAINER_${1}_${2}"; printf '%s' "${!v:-}"; } # <NAME> <SUFFIX>
# A real archive is binary. A 200 carrying a web page or JSON is an SSO/proxy
# login page or an API error, which must never be uploaded as a backup.
is_archive_type() { # <content-type>
  case "$1" in application/gzip*|application/x-gzip*|application/octet-stream*|application/x-tar*) return 0 ;; *) return 1 ;; esac
}
stamp() { date -u +%Y%m%dT%H%M%SZ; }

selftest() {
  check() { [ "$2" = "$3" ] || die "selftest: $1\n  got:  $2\n  want: $3"; }
  local n
  for n in INFRA MY_BOX2 A; do valid_name "$n" || die "selftest: valid_name rejected $n"; done
  for n in '' 'a/b' '..' infra 'MY-BOX' 'A B'; do
    ! valid_name "$n" || die "selftest: valid_name accepted '$n'"
  done
  check slug "$(slug MY_BOX)" my-box
  check backup_key "$(backup_key MY_BOX 20261010T120000Z)" my-box/20261010T120000Z.tar.gz.encrypted
  check backup_body "$(backup_body 'p"w\x y' | jq -r .password)" 'p"w\x y'
  PORTAINER_T_URL=https://t.example
  check inst_var "$(inst_var T URL)" https://t.example
  check inst_var-unset "$(inst_var T NOPE)" ""
  for n in application/gzip application/octet-stream application/x-tar 'application/gzip; charset=binary'; do
    is_archive_type "$n" || die "selftest: is_archive_type rejected '$n'"
  done
  for n in text/html 'text/html; charset=utf-8' application/json application/xhtml+xml ''; do
    ! is_archive_type "$n" || die "selftest: is_archive_type accepted '$n'"
  done
  ! is_set change-me || die "selftest: is_set accepted change-me"
  ! is_set "" || die "selftest: is_set accepted empty"
  is_set x || die "selftest: is_set rejected x"
  case "$(stamp)" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
    *) die "selftest: stamp format: $(stamp)" ;;
  esac
  note "selftest ok"
}

# One call to instance <NAME>'s API. Prints the response; with PAPI_OUT=<file>
# it is written there instead (the backup archive is binary) and the content
# type is printed. Reports on stderr
# and returns 1 rather than exiting, so a bad instance never stops the loop.
# The key reaches curl through a 0600 -K file, never an argument.
papi() { # <NAME> <METHOD> <PATH> [json-body]
  local n="$1" url key cfg out rc
  url="$(inst_var "$n" URL)"; key="$(inst_var "$n" API_KEY)"
  if ! is_set "$url" || ! is_set "$key"; then
    note "$n: set PORTAINER_${n}_URL and PORTAINER_${n}_API_KEY in .portainers.env" >&2
    return 1
  fi
  cfg="$(mktemp)"
  printf 'header = "X-API-Key: %s"\n' "$key" >"$cfg"
  local args=(-sS -K "$cfg" -X "$2" -H "Content-Type: application/json" --data-binary @-)
  if [ -n "${PAPI_OUT:-}" ]; then args+=(--fail -o "$PAPI_OUT" -w '%{content_type}'); else args+=(--fail-with-body); fi
  if [ "$(inst_var "$n" INSECURE)" = true ]; then args+=(-k); fi
  set +e
  out="$(printf '%s' "${4:-}" | curl "${args[@]}" "${url%/}/api$3" 2>&1)"
  rc=$?
  set -e
  rm -f "$cfg"
  if [ "$rc" -ne 0 ]; then
    printf '%s\n' "$out" >&2
    note "$n: $2 $3 failed ($url)" >&2
    return 1
  fi
  printf '%s' "$out" # the response, or with PAPI_OUT its content type
}

cmd_list() {
  local n url ver envs stacks fail=0
  printf '%-12s %-46s %-9s %4s %6s  %s\n' NAME URL VERSION ENVS STACKS STATUS
  for n in $PORTAINERS; do
    url="$(inst_var "$n" URL)"
    if ver="$(papi "$n" GET /system/status | jq -r .Version)" \
       && envs="$(papi "$n" GET /endpoints | jq length)" \
       && stacks="$(papi "$n" GET /stacks | jq length)"; then
      printf '%-12s %-46s %-9s %4s %6s  %s\n' "$n" "$url" "$ver" "$envs" "$stacks" ok
    else
      printf '%-12s %-46s %-9s %4s %6s  %s\n' "$n" "${url:--}" - - - unreachable
      fail=1
    fi
  done
  return "$fail"
}

aws_cli() { # <env-file> <aws args...>
  local envf="$1"; shift
  docker run --rm -i --network "$PORTAINER_NETWORK" --env-file "$envf" \
    "$AWS_CLI_IMAGE" --endpoint-url "$S3_BACKUP_ENDPOINT" "$@"
}

# Fetch <NAME>'s archive into the scratch file, upload it, check the size.
# The scratch file only ever holds Portainer's encrypted archive. An HTTP error
# or an empty body stops here, before anything is uploaded.
backup_one() { # <NAME> <timestamp> <aws-env-file> <scratch-file>
  local n="$1" key size ctype
  key="$(backup_key "$n" "$2")"
  : >"$4"
  ctype="$(PAPI_OUT="$4" papi "$n" POST /backup "$(backup_body "$PORTAINER_BACKUP_PASSWORD")")" || return 1
  if ! is_archive_type "$ctype"; then
    note "$n: Portainer answered '$ctype', not an archive (login page or proxy in the way?)" >&2
    return 1
  fi
  if [ ! -s "$4" ]; then note "$n: Portainer returned an empty archive" >&2; return 1; fi
  if ! aws_cli "$3" s3 cp - "s3://$S3_BACKUP_BUCKET/$key" <"$4" >/dev/null; then
    note "$n: upload to s3://$S3_BACKUP_BUCKET/$key failed" >&2; return 1
  fi
  size="$(aws_cli "$3" s3api head-object --bucket "$S3_BACKUP_BUCKET" --key "$key" \
    --query ContentLength --output text </dev/null)" || size=""
  if [ "$size" != "$(wc -c <"$4" | tr -d ' ')" ]; then
    note "$n: uploaded size '$size' does not match the archive; removing $key" >&2
    aws_cli "$3" s3 rm "s3://$S3_BACKUP_BUCKET/$key" </dev/null >/dev/null || true
    return 1
  fi
  note "$n: s3://$S3_BACKUP_BUCKET/$key ($size bytes)"
}

cmd_backup() {
  local v n ts fail=0
  is_set "${PORTAINER_BACKUP_PASSWORD:-}" ||
    die "set PORTAINER_BACKUP_PASSWORD in .portainers.env (it encrypts the archives)"
  for v in S3_BACKUP_ENDPOINT S3_BACKUP_BUCKET S3_BACKUP_ACCESS_KEY S3_BACKUP_SECRET_KEY; do
    is_set "${!v:-}" || die "set $v in .portainers.env"
  done
  command -v docker >/dev/null || die "missing: docker"
  # Globals, not locals: the EXIT trap runs after this function has returned.
  AWS_ENV="$(mktemp)"; SCRATCH="$(mktemp)"
  trap 'rm -f "$AWS_ENV" "$SCRATCH"' EXIT
  printf 'AWS_ACCESS_KEY_ID=%s\nAWS_SECRET_ACCESS_KEY=%s\nAWS_DEFAULT_REGION=us-east-1\nAWS_EC2_METADATA_DISABLED=true\n' \
    "$S3_BACKUP_ACCESS_KEY" "$S3_BACKUP_SECRET_KEY" >"$AWS_ENV"
  ts="$(stamp)"
  for n in $PORTAINERS; do
    backup_one "$n" "$ts" "$AWS_ENV" "$SCRATCH" || fail=1
  done
  return "$fail"
}

cmd="${1:-}"
case "$cmd" in
  selftest) selftest; exit 0 ;;
  list|backup) ;;
  *) die "usage: scripts/portainer-backup.sh list|backup|selftest" ;;
esac

command -v jq >/dev/null || die "missing: jq"
command -v curl >/dev/null || die "missing: curl"

# .portainers.env is optional so the variables can also come from the
# environment; PORTAINERS is what is required.
if [ -f .portainers.env ]; then set -a; . ./.portainers.env; set +a; fi
[ -n "${PORTAINERS:-}" ] || die "set PORTAINERS in .portainers.env (see .portainers.env.example)"
for n in $PORTAINERS; do
  valid_name "$n" || die "bad instance name '$n' in PORTAINERS (use A-Z, 0-9 and _)"
done
PORTAINER_NETWORK="${PORTAINER_NETWORK:-infra-net}"

case "$cmd" in
  list) cmd_list ;;
  backup) cmd_backup ;;
esac
