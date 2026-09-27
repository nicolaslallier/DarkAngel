# MinIO → SeaweedFS — DarkAngel follow-up

Date: 2026-09-27
Status: approved design, awaiting spec review

## Why

The Infra stack replaced MinIO with SeaweedFS (Infra PR #70, `2a56fc7`; design in
`Infra/docs/superpowers/specs/2026-09-26-minio-to-seaweedfs-design.md`). Infra kept
no `minio` alias and migrated no data: *"Fresh start, no data migration. Apps
re-upload."* Until DarkAngel follows, the Files page is down in production — the
backend still dials `minio:9000`, which no longer resolves on `infra-net`.

Infra's cutover step 5 names what DarkAngel owes (`:346`): endpoint
`http://s3:8333`, access key `darkangel` (was `darkangel-api`), and delete
`make minio` because Infra's `make s3-provision` now owns provisioning.

## Goals

- The Files page works against the Infra SeaweedFS, with bucket versioning on.
- No MinIO left anywhere in the repo: code names, scripts, tests, CI, docs.
- Integration tests and CI run against the same SeaweedFS release Infra pins.
- An unversioned bucket is caught on the first upload, not in Phase 3.

## Non-goals

- Migrating bytes or versions from MinIO (Infra's decision: fresh start).
- Swapping the S3 SDK. `minio-py` is a generic S3 client; it stays.
- Any SeaweedFS feature beyond the plain S3 API (filer, STS, Iceberg).
- EA, Jarvis, Obsidian — other repos, their own follow-ups.

## Facts from Infra (verified on Infra `main`)

| | Value |
|---|---|
| Service on `infra-net` | `s3`, S3 gateway on plain HTTP `:8333` |
| Public endpoint | `https://s3.infra.famillelallier.net` (NGINX, TLS, no body limit) |
| Image | `chrislusf/seaweedfs:4.47@sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882` |
| Provisioning | `make s3-provision app=darkangel bucket=darkangel-files versioned=1` |
| Identity | user = access key = `darkangel`; actions `Read,Write,List,Tagging` on the bucket |
| Secret | `DARKANGEL_S3_SECRET_KEY` in the Infra `.env` |
| Health | `GET /healthz`, unauthenticated |

**Infra spec gap.** Infra's per-app table (`:221`) provisions DarkAngel *without*
`versioned=1`. DarkAngel records `s3_version_id` for every upload and Phase 3
restore depends on it, so DarkAngel must be provisioned with `versioned=1`. Fix
that row in the Infra spec as a one-line docs change in the Infra repo.

## Design

### 1. Runtime config

`backend/app/core/config.py`:

```python
# Home files, in the Infra SeaweedFS: plain HTTP `s3:8333` on infra-net. The
# bucket and its scoped identity are made by Infra's `make s3-provision`.
s3_endpoint: str = "s3:8333"
s3_secure: bool = False
s3_bucket: str = "darkangel-files"
s3_access_key: str = "darkangel"
s3_secret_key: str = ""
```

`backend/.env.example` follows. From a laptop:
`DARKANGEL_S3_ENDPOINT=s3.infra.famillelallier.net` with `DARKANGEL_S3_SECURE=true`.

### 2. Neutral names (approach 2)

MinIO no longer exists anywhere DarkAngel runs, so names that say "minio" are
renamed. The `minio` pip dependency is the one exception — it is the SDK.

| Before | After |
|---|---|
| `files.minio_client()` | `files.s3_client()` |
| `FakeMinio` (`tests/conftest.py`) | `FakeS3` |
| fixture `minio_bucket` | `s3_bucket` |
| `tests/integration/test_files_minio.py` | `test_files_s3.py` |
| `test_minio_issues_a_real_version_id` | `test_the_store_issues_a_real_version_id` |
| operator var `MINIO_SECRET_KEY` | `S3_SECRET_KEY` |
| GitHub secret `MINIO_SECRET_KEY` | `S3_SECRET_KEY` |

`S3_SECRET_KEY` changes in `.portainer.env.example`, `deploy/portainer-stack.yml`
(`DARKANGEL_S3_SECRET_KEY: ${S3_SECRET_KEY:-}`), `scripts/portainer-stack.sh`
(`stack_env`, its self-test, the "not set" warning) and `.github/workflows/deploy.yml`.
The GitHub secret was never set (every deploy since #21 fails for that reason),
so the rename breaks nothing already working. Its value is the Infra
`.env`'s `DARKANGEL_S3_SECRET_KEY`.

Comments and docstrings saying "MinIO" become "object storage" or "S3"; the
wording is otherwise unchanged. `pyproject.toml`'s `integration` marker
description becomes "needs real S3 storage + Postgres".

### 3. Provisioning moves to Infra

Delete `scripts/provision-minio.sh` and the `minio` Makefile target (and its
`.PHONY` entry). README's bootstrap step becomes:

> **Provision file storage** — on the Docker host, in the Infra repo, set
> `DARKANGEL_S3_SECRET_KEY` in its `.env` and run
> `make s3-provision app=darkangel bucket=darkangel-files versioned=1`. Put the
> same value in DarkAngel's `.portainer.env` as `S3_SECRET_KEY` and in the
> `S3_SECRET_KEY` GitHub secret.

`scripts/provision-postgres.sh`'s header comment stops citing
`provision-minio.sh` as its model.

### 4. A missing version id fails the upload

Both `put_object` call sites in `api/routes/files.py` do
`s3_version_id=written.version_id or ""`. On an unversioned bucket that records
empty strings silently and Phase 3 restore finds nothing to restore.

New behaviour: if `written.version_id` is falsy, log
`"bucket %s returned no version id; is versioning enabled?"` and answer `502`,
exactly as the existing `except S3Error` branch next to each call does:

- New file (`upload_file`): `repo.abandon(row)` releases the reservation, then a
  best-effort `remove_object` drops the unversioned bytes just written.
- New version (`_append_version`): `502`, no `add_version`. On an unversioned
  bucket the previous bytes are already overwritten — unrecoverable, which is
  why the error must surface on the very first upload after cutover.

Unit test: `FakeS3.put_object` returning `version_id=None` makes a new-file
upload return `502` and leaves nothing listed, and a same-name upload return
`502` without adding a version.

### 5. Tests and CI on SeaweedFS

`docker-compose.test.yml` — `minio` service becomes:

```yaml
s3:
  # Same release, same digest as the Infra stack's `s3` service.
  image: chrislusf/seaweedfs:4.47@sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882
  command: server -ip.bind=0.0.0.0 -s3 -s3.port.iceberg=0 -s3.port.lance=0
  ports:
    - "8333:8333"
  environment:
    AWS_ACCESS_KEY_ID: s3admin
    AWS_SECRET_ACCESS_KEY: s3admin-secret
  healthcheck:
    test: ["CMD", "wget", "-qO-", "http://127.0.0.1:8333/healthz"]
```

`.github/workflows/ci.yml` `backend-integration`: same image via `docker run`
(port 8333, same env), readiness loop on `http://localhost:8333/healthz`,
`docker logs s3` on failure, job and step names say "S3"/"SeaweedFS". The
Chainguard MinIO pin and its comment go.

`tests/integration/conftest.py` `DEFAULTS`: `localhost:8333`,
`s3admin` / `s3admin-secret`. The fixture keeps its shape — throwaway bucket,
`set_bucket_versioning(ENABLED)`, versioned teardown.

Makefile `test-integration` / `services-test-up` help text: "MinIO" → "S3".
`docs/testing.md`: every MinIO mention, the port, the skip message example, the
`docker logs` troubleshooting line.

**Gate — first task of the plan.** Before any other change, run the existing
integration suite, unmodified except for the endpoint and credentials, against
SeaweedFS 4.47. It must prove: `make_bucket`, `set_bucket_versioning`,
`put_object` returning a non-empty `version_id`, two uploads leaving two
versions, multipart-sized uploads, `get_object` 404 as `NoSuchKey`,
`list_objects(include_version=True)` and versioned `remove_object` in teardown,
and that the compose healthcheck command (`wget`) exists in the image.
Any failure stops the plan for a design decision; it is not patched around in
the tests.

### 6. Cutover runbook

Order matters: the backend starts dialing `s3:8333` as soon as the merge
deploys, and the reset must run *before* that. Run after, it would also wipe
anything uploaded in between — rows and version records gone, bytes orphaned.
Running it first costs nothing: Files is already down until the merge.

1. Infra: set `DARKANGEL_S3_SECRET_KEY` in the Infra `.env`, run
   `make s3-provision app=darkangel bucket=darkangel-files versioned=1`.
2. DarkAngel: set `S3_SECRET_KEY` in `.portainer.env` and as a GitHub secret
   (`gh secret set S3_SECRET_KEY`).
3. `make files-reset` (from this branch) — empties the metadata that points at
   bytes that no longer exist (below).
4. Merge the PR; `deploy.yml` redeploys the stack.
5. Smoke test on the deployed app: upload a file, download it, and check its
   `file_versions.s3_version_id` is non-empty.

`make files-reset` runs `scripts/files-reset.sh`, which uses `PGADMIN_URL` (as
`make postgres` does) against the `darkangel` database:

```sql
TRUNCATE files, file_versions;
```

- `file_versions` references `files` — both go in one statement.
- `folders` is kept: it points at no bytes, and an empty folder tree is still true.
- `audit_log` is kept: it is history, has no foreign key, and rows naming
  vanished file ids are simply never shown.

The script prints the row counts it is about to drop and refuses to run unless
`CONFIRM=darkangel` is set, mirroring the backfill script's refusal. It is a
one-time tool; delete it in a later PR once the cutover is done.

## Error handling

| Situation | Behaviour |
|---|---|
| `s3:8333` unreachable | Unchanged: uploads/downloads `502`, listings still work (SQL only) |
| Bucket not versioned | Upload `502` with a log line naming versioning (§4) |
| Wrong/missing `S3_SECRET_KEY` | `AccessDenied` → `502`; `portainer-stack.sh` warns at deploy time when unset |
| Old rows left if `files-reset` is skipped | Download `404` (NoSuchKey, pinned by `test_a_row_whose_object_is_gone_downloads_as_404`) |

## Files touched

- `backend/app/core/config.py`, `backend/.env.example`, `backend/pyproject.toml`
- `backend/app/api/routes/files.py`, `backend/app/scripts/backfill.py`,
  `backend/app/repositories/files.py` (comments)
- `backend/tests/conftest.py`, `backend/tests/integration/*`,
  `backend/tests/unit/test_files.py`, `backend/tests/regression/*` (renames/comments)
- `docker-compose.test.yml`, `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`
- `deploy/portainer-stack.yml`, `.portainer.env.example`, `scripts/portainer-stack.sh`
- `scripts/provision-minio.sh` (deleted), `scripts/files-reset.sh` (new),
  `scripts/provision-postgres.sh` (comment)
- `Makefile`, `README.md`, `CLAUDE.md`, `docs/testing.md`, `docs/files-feature.md`
- `frontend/tests/regression/pr12-download-blob-url.test.ts` (comment only)

Historical specs and plans under `docs/superpowers/` are left as written.

## Success criteria

- `git grep -i minio` matches only `backend/pyproject.toml`'s `minio` dependency,
  imports of the `minio` package, and `docs/superpowers/`.
- `make verify` and the CI `backend-integration` job pass against SeaweedFS 4.47.
- After cutover, an upload on the deployed app records a non-empty `s3_version_id`.
