# Portainer: list instances and back them up to SeaweedFS

Date: 2026-10-10 · Status: draft, awaiting review

## Intent

The owner runs several Portainer instances (the Infra one at
`portainer.infra.famillelallier.net` and others). They want to **list** them and
**back each one up** with Portainer's native backup, stored in the Infra
SeaweedFS. Used from the command line (`make`), not from the DarkAngel app.

Success: one command shows every configured instance and whether it answers; one
command produces an encrypted archive per instance in the `portainer-backups`
bucket, and a failure on one instance neither hides nor blocks the others.

Out of scope (YAGNI): UI/backend/DB, scheduling, rotation (use a SeaweedFS
lifecycle rule if wanted), backing up stack contents separately from the native
archive.

## Components

### `.portainers.env` (gitignored) + `.portainers.env.example`

One block per instance, names in `NAME` form (`[A-Z0-9_]+`):

```
PORTAINERS="INFRA MAISON"
PORTAINER_INFRA_URL=https://portainer.infra.famillelallier.net
PORTAINER_INFRA_API_KEY=change-me
PORTAINER_MAISON_URL=https://...
PORTAINER_MAISON_API_KEY=change-me
#PORTAINER_MAISON_INSECURE=true      # optional, self-signed cert

# Shared by every backup
PORTAINER_BACKUP_PASSWORD=change-me   # required: encrypts the archive
S3_BACKUP_ENDPOINT=http://s3:8333     # as seen from infra-net
S3_BACKUP_BUCKET=portainer-backups
S3_BACKUP_ACCESS_KEY=portainer-backups
S3_BACKUP_SECRET_KEY=change-me
PORTAINER_NETWORK=infra-net           # optional, default infra-net
```

API keys are Docker-root tokens: they stay in this file and reach curl through a
`-K` config file (0600 via `mktemp`), never as arguments, like
`api_direct` in `scripts/portainer-stack.sh`.

### `scripts/portainer-backup.sh list|backup|selftest`

- **list**: for each instance, `GET /api/system/status` (version) and
  `GET /api/endpoints` + `GET /api/stacks` (counts). Prints
  `name  url  version  environments  stacks  ok|unreachable`. Unreachable
  instances are reported and skipped; exit non-zero if any was unreachable.
- **backup**: refuses to run if `PORTAINER_BACKUP_PASSWORD` is unset or
  `change-me`. For each instance, `POST /api/backup` with
  `{"password": "<PORTAINER_BACKUP_PASSWORD>"}`; the response (the encrypted
  archive) is written to a 0600 temp file (so an HTTP error is never uploaded
  as an archive), then uploaded with
  `aws s3 cp - s3://portainer-backups/<instance>/<UTC timestamp>.tar.gz.encrypted`,
  run via the `amazon/aws-cli` image (pinned tag) on `PORTAINER_NETWORK` with
  `--endpoint-url "$S3_BACKUP_ENDPOINT"`. Nothing is written unencrypted to
  disk. S3 credentials go to the container through a 0600 temp env-file removed
  on exit, not through `-e` of the environment (the WSL caveat documented in
  `portainer-stack.sh`).
- A failed instance is logged and the loop continues; the final exit status is
  non-zero if any failed. The upload is checked with `aws s3 ls` of the key
  (size > 0) before reporting success.
- **selftest**: pure helpers only (instance-name validation, config parsing,
  object-key naming, the JSON body built for `/api/backup`), no network.

### Makefile

`portainers` (list), `portainer-backup` (backup), `portainer-backup-selftest`.
`make help` picks them up through their `##` comments. `.gitignore` gains
`.portainers.env`.

### Infra prerequisite

A bucket `portainer-backups` and a scoped S3 identity, created like the
existing one: `make s3-provision app=portainer-backups bucket=portainer-backups`
in the Infra repo (versioned recommended). Documented in the README; not done by
this change.

### Docs

README section "Backing up Portainer" with the config, the two commands, and the
restore procedure (Portainer restores an archive at first setup or via
`--restore`; the password is needed — lose it and the archive is unusable).

## Errors and security

- Missing/placeholder key, URL or password: clear message, no request sent.
- Portainer backup endpoint errors (HTTP 4xx/5xx) surface the response body on
  stderr, as `api_direct` does.
- Archive and keys never reach a log; no instance URL carries credentials.
- The instance name is validated before use in S3 keys (no `/` or `..`).

## Testing

`make portainer-backup-selftest` covers the helpers. The network path is
verified manually once against a real instance (list, then backup, then
`aws s3 ls` of the key), since CI has no Portainer.

## Open choices, defaulted

- aws-cli image tag: pin the current stable `amazon/aws-cli` at implementation
  time.
- Timestamp format: `YYYYMMDDTHHMMSSZ` (sortable, S3-safe).
