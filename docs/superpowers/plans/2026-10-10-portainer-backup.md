# Portainer list + backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `make portainers` lists every configured Portainer instance; `make portainer-backup` stores each instance's native, password-encrypted backup in the SeaweedFS bucket `portainer-backups`.

**Architecture:** One bash script, `scripts/portainer-backup.sh`, in the style of `scripts/portainer-stack.sh` (same `die`/`note`, same `curl -K <0600 file>` key handling, same `selftest` command). Instances come from a gitignored `.portainers.env`. Backups are fetched with curl into a 0600 scratch file (Portainer already encrypts the archive), then uploaded with the `amazon/aws-cli` image run on `infra-net`, then verified by size.

**Tech Stack:** bash (macOS 3.2 compatible), curl, jq, docker (for aws-cli only), make.

**Spec:** `docs/superpowers/specs/2026-10-10-portainer-backup-design.md`

## Global Constraints

- No backend, frontend or database change; script + Makefile + docs only.
- API keys and the S3 secret never appear on a command line or in a log: curl gets the key through `-K <mktemp file>`, docker gets S3 credentials through a 0600 `--env-file` removed on exit.
- Refuse to back up unless `PORTAINER_BACKUP_PASSWORD` is set and not `change-me`.
- Instance names match `[A-Z0-9_]+` (they become S3 key prefixes, lowercased, `_` → `-`).
- Object key: `<instance>/<UTC YYYYMMDDTHHMMSSZ>.tar.gz.encrypted` in bucket `portainer-backups`.
- A failure on one instance never stops the others; the final exit status is non-zero if any failed.
- No rotation, no scheduling, no UI (YAGNI).
- Repo rule (CLAUDE.md): work on this branch, never on `main`; **commit only when the user asks** — the "Commit" steps below are for that moment.
- One deviation from the spec text: the archive is written to a 0600 temp file (encrypted content) before upload instead of being piped, so an HTTP error can never be uploaded as if it were an archive and the size can be verified.

## Review Focus

- Instance name `a/b`, `..`, lowercase or empty must be rejected before it can reach an S3 key (Task 1 selftest).
- Backup password containing `"`, `\` or spaces must reach Portainer intact as JSON (Task 1 selftest).
- `backup` without a usable `PORTAINER_BACKUP_PASSWORD` must fail before any request is made (Task 2 offline check).
- An unreachable instance, or one missing its URL/key, is reported and the loop continues; exit status is non-zero (Task 2 offline check).
- An empty archive or an HTTP error from Portainer must not create an S3 object (Task 2: scratch-file check precedes the upload).

## File Structure

- Create `scripts/portainer-backup.sh` — all logic (helpers, `list`, `backup`, `selftest`).
- Create `.portainers.env.example` — documented config template.
- Modify `.gitignore` — ignore `.portainers.env`.
- Modify `Makefile` — three targets + `.PHONY` entries.
- Modify `README.md` — "Backing up Portainer" section under Deployment.

---

### Task 1: Script skeleton, pure helpers and selftest

**Files:**
- Create: `scripts/portainer-backup.sh`
- Create: `.portainers.env.example`
- Modify: `.gitignore` (after the `.portainer.env` line, ~line 21)

**Interfaces:**
- Produces (used by Task 2), all pure shell functions:
  - `is_set <value>` → 0 if non-empty and not `change-me`
  - `valid_name <NAME>` → 0 if `[A-Z0-9_]+`
  - `slug <NAME>` → lowercased, `_`→`-`
  - `backup_key <NAME> <timestamp>` → `<slug>/<timestamp>.tar.gz.encrypted`
  - `backup_body <password>` → `{"password": ...}` JSON
  - `inst_var <NAME> <SUFFIX>` → value of `PORTAINER_<NAME>_<SUFFIX>` (empty if unset)
  - `stamp` → `YYYYMMDDTHHMMSSZ` (UTC)
  - `die`, `note`

- [ ] **Step 1: Write the script with the selftest only (helpers missing)**

Create `scripts/portainer-backup.sh`:

```bash
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
  ! is_set change-me || die "selftest: is_set accepted change-me"
  ! is_set "" || die "selftest: is_set accepted empty"
  is_set x || die "selftest: is_set rejected x"
  case "$(stamp)" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
    *) die "selftest: stamp format: $(stamp)" ;;
  esac
  note "selftest ok"
}

case "${1:-}" in
  selftest) selftest; exit 0 ;;
  *) die "usage: scripts/portainer-backup.sh list|backup|selftest" ;;
esac
```

Run: `chmod +x scripts/portainer-backup.sh`

- [ ] **Step 2: Run it to verify it fails**

Run: `scripts/portainer-backup.sh selftest`
Expected: FAIL — `valid_name: command not found` (non-zero exit).

- [ ] **Step 3: Add the helpers**

Insert between `note()` and `selftest()`:

```bash
is_set() { [ -n "${1:-}" ] && [ "$1" != change-me ]; }
valid_name() { case "$1" in ''|*[!A-Z0-9_]*) return 1 ;; *) return 0 ;; esac; }
slug() { printf '%s' "$1" | tr 'A-Z_' 'a-z-'; }
backup_key() { printf '%s/%s.tar.gz.encrypted' "$(slug "$1")" "$2"; } # <NAME> <timestamp>
backup_body() { jq -n --arg p "$1" '{password: $p}'; }
inst_var() { local v="PORTAINER_${1}_${2}"; printf '%s' "${!v:-}"; } # <NAME> <SUFFIX>
stamp() { date -u +%Y%m%dT%H%M%SZ; }
```

- [ ] **Step 4: Run the selftest to verify it passes**

Run: `scripts/portainer-backup.sh selftest`
Expected: `portainer-backup.sh: selftest ok`

- [ ] **Step 5: Config template and gitignore**

Create `.portainers.env.example`:

```bash
# Copy to .portainers.env (gitignored) and fill in. Used by
# scripts/portainer-backup.sh, i.e. `make portainers` and `make portainer-backup`.
#
# The API keys are Docker-daemon-root tokens: keep them here, never in git.
# Create one per instance under Portainer -> My account -> Access tokens.

# Instance names: A-Z, 0-9 and _ only. Each needs the two variables below.
PORTAINERS="INFRA MAISON"

PORTAINER_INFRA_URL=https://portainer.infra.famillelallier.net
PORTAINER_INFRA_API_KEY=change-me

PORTAINER_MAISON_URL=https://portainer.example.net
PORTAINER_MAISON_API_KEY=change-me
#PORTAINER_MAISON_INSECURE=true     # optional: self-signed certificate

# Shared by every backup. The password encrypts each archive: without it an
# archive cannot be restored, so store it somewhere other than this file too.
PORTAINER_BACKUP_PASSWORD=change-me

# SeaweedFS destination, as seen from PORTAINER_NETWORK. Bucket and identity are
# made by the Infra repo: make s3-provision app=portainer-backups bucket=portainer-backups
S3_BACKUP_ENDPOINT=http://s3:8333
S3_BACKUP_BUCKET=portainer-backups
S3_BACKUP_ACCESS_KEY=portainer-backups
S3_BACKUP_SECRET_KEY=change-me

# Optional: docker network that reaches the S3 endpoint (default infra-net).
#PORTAINER_NETWORK=infra-net
```

In `.gitignore`, after the `.portainer.env` line add:

```
# Portainer instances + keys for scripts/portainer-backup.sh; see .portainers.env.example
.portainers.env
```

- [ ] **Step 6: Commit (only when the user asks)**

```bash
git add scripts/portainer-backup.sh .portainers.env.example .gitignore
git commit -m "feat: portainer-backup helpers, selftest and config template"
```

---

### Task 2: `list` and `backup` commands, Makefile, README

**Files:**
- Modify: `scripts/portainer-backup.sh`
- Modify: `Makefile` (`.PHONY` line 42; targets after `stack-selftest`, ~line 249)
- Modify: `README.md` (new subsection after "Running the stack", ~line 316)

**Interfaces:**
- Consumes: `is_set`, `valid_name`, `backup_key`, `backup_body`, `inst_var`, `stamp`, `die`, `note` from Task 1.
- Produces: `cmd_list` / `cmd_backup` (exit 0 only if every instance succeeded); `papi <NAME> <METHOD> <PATH> [json-body]` (prints the response, or with `PAPI_OUT=<file>` writes it there; returns 1 after a message on stderr, never exits); `aws_cli <env-file> <aws args...>`.

- [ ] **Step 1: Write the offline checks first (they fail: commands don't exist)**

Run each; all currently print the usage error and exit 1:

```bash
# list: two unusable instances -> both reported, exit 1, loop did not stop at the first
PORTAINERS="A B" PORTAINER_A_URL=http://127.0.0.1:1 PORTAINER_A_API_KEY=k \
  scripts/portainer-backup.sh list; echo "exit=$?"

# backup without a password -> refused before any request
PORTAINERS="A" PORTAINER_BACKUP_PASSWORD=change-me scripts/portainer-backup.sh backup; echo "exit=$?"
```

Expected after Step 3: first prints a header and two `unreachable` rows (A: connection refused; B: not configured) and `exit=1`; second prints `set PORTAINER_BACKUP_PASSWORD` and `exit=1` with no curl/docker call.

- [ ] **Step 2: Add the API helper, `list` and `backup`**

Replace the final `case "${1:-}" ...` block of the script with the code below (keep `selftest()` and helpers above it):

```bash
# One call to instance <NAME>'s API. Prints the response; with PAPI_OUT=<file>
# it is written there instead (the backup archive is binary). Reports on stderr
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
  if [ -n "${PAPI_OUT:-}" ]; then args+=(--fail -o "$PAPI_OUT"); else args+=(--fail-with-body); fi
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
  if [ -z "${PAPI_OUT:-}" ]; then printf '%s' "$out"; fi
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
  local n="$1" key size
  key="$(backup_key "$n" "$2")"
  : >"$4"
  PAPI_OUT="$4" papi "$n" POST /backup "$(backup_body "$PORTAINER_BACKUP_PASSWORD")" || return 1
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
```

- [ ] **Step 3: Run the offline checks and the selftest**

Run the two commands from Step 1, then `scripts/portainer-backup.sh selftest`.
Expected: as described in Step 1 (two `unreachable` rows with `exit=1`; the password refusal with `exit=1`), then `selftest ok`. Also run `bash -n scripts/portainer-backup.sh` (no output) and, if installed, `shellcheck scripts/portainer-backup.sh`.

- [ ] **Step 4: Makefile targets**

In `Makefile`, add `portainers portainer-backup portainer-backup-selftest` to the `.PHONY` list on the `up pull down delete webhook stack-selftest ...` line, and after the `stack-selftest` target add:

```make
BACKUP_SH := ./scripts/portainer-backup.sh

portainers: ## List the Portainer instances in .portainers.env (version, environments, stacks, reachability)
	@$(BACKUP_SH) list

portainer-backup: ## Back up every Portainer instance to SeaweedFS (bucket portainer-backups, encrypted)
	@$(BACKUP_SH) backup

portainer-backup-selftest: ## Check portainer-backup.sh's helpers without calling Portainer or S3
	@$(BACKUP_SH) selftest
```

Run: `make portainer-backup-selftest && make help | grep -i portainer`
Expected: `selftest ok`, and the three new targets appear in the help.

- [ ] **Step 5: README section**

In `README.md`, after the "Running the stack" subsection (before "Deploying from a self-hosted runner"), add:

````markdown
### Backing up Portainer

`make portainers` lists every Portainer instance in `.portainers.env` (version,
environments, stacks, reachable or not) and `make portainer-backup` stores one
encrypted native Portainer backup per instance in the Infra SeaweedFS, at
`s3://portainer-backups/<instance>/<UTC timestamp>.tar.gz.encrypted`.

```sh
cp .portainers.env.example .portainers.env   # fill in URLs, API keys, password, S3 secret
make portainers
make portainer-backup
```

One-time, in the Infra repo: `make s3-provision app=portainer-backups
bucket=portainer-backups versioned=1`, then put that identity's secret in
`S3_BACKUP_SECRET_KEY`. One failing instance does not stop the others; the
command exits non-zero if any failed. There is no rotation: add a lifecycle
rule on the bucket if old archives should expire.

**Restore:** an archive restores a fresh Portainer at its first setup screen
("Restore Portainer from backup") or with the `--restore` flag, and needs
`PORTAINER_BACKUP_PASSWORD`. Lose the password and the archive is unusable, so
keep it somewhere other than `.portainers.env`.
````

- [ ] **Step 6: Manual end-to-end check (needs a real instance and the Infra S3)**

1. `docker pull amazon/aws-cli:2.17.30` — if the tag does not exist, pick a current `2.x` tag and update `AWS_CLI_IMAGE` in the script.
2. Fill `.portainers.env` with one real instance. `make portainers` → one `ok` row with the right version.
3. `make portainer-backup` → `<NAME>: s3://portainer-backups/<name>/<stamp>.tar.gz.encrypted (<N> bytes)`.
4. Confirm with `docker run --rm --network infra-net --env-file <creds> amazon/aws-cli --endpoint-url http://s3:8333 s3 ls s3://portainer-backups/ --recursive`. If aws-cli fails with a DNS error on `portainer-backups.s3`, it chose virtual-host addressing: add `AWS_S3_ADDRESSING_STYLE=path` support by mounting a config file containing `[default]\ns3 =\n    addressing_style = path` and setting `AWS_CONFIG_FILE`, and record that in the script.
5. Break the password (`change-me`) → refused; break one API key with two instances → one fails, the other still uploads, exit status 1.

- [ ] **Step 7: Commit (only when the user asks)**

```bash
git add scripts/portainer-backup.sh Makefile README.md
git commit -m "feat: list Portainer instances and back them up to SeaweedFS"
```
