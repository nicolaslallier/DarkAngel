# MinIO → SeaweedFS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Point DarkAngel's home-file storage at the Infra SeaweedFS (`s3:8333`), remove every MinIO leftover, and run the integration suite and CI against SeaweedFS.

**Architecture:** The backend already speaks plain S3 through the `minio` pip package, which stays. The work is config defaults, neutral renames, one new failure mode (no version id → `502`), test/CI infrastructure, deploy plumbing, a one-time `make files-reset`, and docs. Provisioning moves out to Infra's `make s3-provision`.

**Tech Stack:** FastAPI, `minio` (S3 SDK), SQLAlchemy/PostgreSQL, pytest, GitHub Actions, Docker Compose, SeaweedFS 4.47, bash + `psql`.

**Spec:** `docs/superpowers/specs/2026-09-27-minio-to-seaweedfs-design.md`

## Global Constraints

- SeaweedFS image, everywhere: `chrislusf/seaweedfs:4.47@sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882`
- Production endpoint `s3:8333`, plain HTTP; access key `darkangel`; bucket `darkangel-files`.
- Test/CI endpoint `localhost:8333`; root credentials `s3admin` / `s3admin-secret`.
- Operator variable and GitHub secret: `S3_SECRET_KEY` (was `MINIO_SECRET_KEY`).
- Renames: `minio_client` → `s3_client`, `FakeMinio` → `FakeS3`, fixture `minio_bucket` → `s3_bucket`, `test_files_minio.py` → `test_files_s3.py`.
- The `minio` pip dependency and its imports stay.
- `docs/superpowers/**` historical specs/plans are never edited.
- Backend tools always run through `backend/.venv/bin/`; ruff line length 100.
- CLAUDE.md: work on this branch, never on `main`; changes reach `main` only by PR.

## Review Focus

- **Unversioned bucket in production** (Infra's spec omits `versioned=1`) — the first upload must fail with `502` and a log line naming versioning, not record an empty version id. Pinned by Task 3's tests.
- **Scoped `darkangel` identity vs admin** — the suite runs as admin, so a missing permission (MinIO's `GetBucketLocation` trap) is invisible to tests. Pinned by Task 1 Step 5, a manual upload as a `Read,Write,List,Tagging` identity.
- **Download of a missing object** — SeaweedFS must answer `NoSuchKey` so the route maps it to `404`, not `502`. Pinned by `test_downloading_a_missing_file_is_404` running on SeaweedFS in Task 1.
- **Multipart-sized upload (>10 MiB)** — must round-trip byte-exact through SeaweedFS. Pinned by `test_a_multipart_sized_upload_survives_the_round_trip` in Task 1.
- **Deploy with `S3_SECRET_KEY` unset** — the deploy must warn, not silently blank the key. Pinned by the updated `stack_env` self-test and warning text in Task 4.

---

### Task 1: Integration suite and CI on SeaweedFS (the gate)

The spec's hard gate: prove SeaweedFS 4.47 does everything the suite needs **before** anything else changes. If any step here fails for a SeaweedFS reason, STOP and report; do not adapt tests around it.

**Files:**
- Modify: `docker-compose.test.yml:1-24` (the `minio` service and header comment)
- Modify: `backend/tests/integration/conftest.py:14-21` (`DEFAULTS` and its comment)
- Modify: `.github/workflows/ci.yml:67-145` (`backend-integration` job)
- Modify: `Makefile:154,163` (help text)

**Interfaces:**
- Consumes: nothing.
- Produces: a SeaweedFS at `localhost:8333` (compose service `s3`, CI container `s3`) with root `s3admin` / `s3admin-secret`. Later tasks run `make services-test-up && make test-integration` against it.

- [ ] **Step 1: Install and start from a clean state**

Run: `make install && docker compose -f docker-compose.test.yml down -v`
Expected: install succeeds; the `down` succeeds or reports nothing to remove.

- [ ] **Step 2: Replace the MinIO service in `docker-compose.test.yml`**

Replace the header's first line and the whole `minio:` service with:

```yaml
# SeaweedFS and Postgres, for the integration suite. Nothing else in this project
```

```yaml
  s3:
    # Same release and digest as the Infra stack's `s3` service, so the suite
    # proves the store production runs. Root credentials come from the AWS_*
    # variables; without them SeaweedFS serves anonymously.
    image: chrislusf/seaweedfs:4.47@sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882
    command: server -ip.bind=0.0.0.0 -s3 -s3.port.iceberg=0 -s3.port.lance=0
    ports:
      - "8333:8333"
    environment:
      AWS_ACCESS_KEY_ID: s3admin
      AWS_SECRET_ACCESS_KEY: s3admin-secret
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:8333/healthz"]
      interval: 2s
      timeout: 3s
      retries: 30
```

The `postgres` service is unchanged.

- [ ] **Step 3: Point the integration defaults at it**

In `backend/tests/integration/conftest.py`:

```python
# Matches docker-compose.test.yml and the CI step. Exporting any of these before
# the run points the suite at another S3 store instead.
DEFAULTS = {
    "DARKANGEL_S3_ENDPOINT": "localhost:8333",
    "DARKANGEL_S3_ACCESS_KEY": "s3admin",
    "DARKANGEL_S3_SECRET_KEY": "s3admin-secret",
    "DARKANGEL_S3_SECURE": "false",
}
```

Makefile help text: line 154 `(needs MinIO + Postgres; ...)` → `(needs S3 + Postgres; ...)`; line 163 `(MinIO + Postgres)` → `(SeaweedFS + Postgres)`.

- [ ] **Step 4: Run the whole integration suite against SeaweedFS**

Run: `make services-test-up && make test-integration ARGS=-rs`
Expected: `docker compose ... --wait` returns healthy (this also proves `wget` exists in the image); every integration test passes, **0 skipped**. That covers, on the real store: `make_bucket`, `set_bucket_versioning`, a non-empty `version_id` (`test_minio_issues_a_real_version_id`), two versions after two uploads, the multipart-sized round trip, `NoSuchKey` → `404`, and the versioned teardown (`list_objects(include_version=True)` + `remove_object(version_id=...)` + `remove_bucket`).

If the healthcheck never passes, run `docker compose -f docker-compose.test.yml logs s3`. If `wget` is missing, switch the healthcheck to `["CMD", "curl", "-fsS", "http://127.0.0.1:8333/healthz"]` and rerun. Any **test** failure: STOP and report the failing test and its output; the spec requires a design decision.

- [ ] **Step 5: Prove a scoped identity can do the app's calls (manual, not committed)**

The suite runs as admin; production runs as `darkangel` with `Read,Write,List,Tagging` on one bucket. Reproduce that on the test SeaweedFS:

```bash
docker compose -f docker-compose.test.yml exec -T s3 sh -c \
  "printf 's3.bucket.create -name darkangel-files\ns3.bucket.versioning -name darkangel-files -enable\ns3.configure -user darkangel -access_key darkangel -secret_key darkangel-secret -buckets darkangel-files -actions Read,Write,List,Tagging -apply\n' | weed shell -master=localhost:9333"
cd backend && .venv/bin/python - <<'PY'
import io
from minio import Minio
c = Minio("localhost:8333", access_key="darkangel", secret_key="darkangel-secret", secure=False)
w = c.put_object("darkangel-files", "probe/a.txt", io.BytesIO(b"hi"), length=2)
assert w.version_id, "no version id"
assert c.get_object("darkangel-files", "probe/a.txt").read() == b"hi"
c.remove_object("darkangel-files", "probe/a.txt")
print("scoped identity OK, version", w.version_id)
PY
```

Expected: `scoped identity OK, version <non-empty>`. An `AccessDenied` here is a production outage waiting to happen: STOP and report which call failed. Then `make services-test-down` to drop the probe bucket.

- [ ] **Step 6: Switch the CI job**

In `.github/workflows/ci.yml`, `backend-integration`:

- `name: Backend integration (real MinIO + Postgres)` → `name: Backend integration (real SeaweedFS + Postgres)`
- The Postgres services comment → `# A service container works here, unlike SeaweedFS: the postgres image needs`
- `env:` block:

```yaml
    env:
      # `CI` makes an unreachable S3 store or Postgres fail the job, not skip it.
      CI: "true"
      DARKANGEL_S3_ENDPOINT: localhost:8333
      DARKANGEL_S3_ACCESS_KEY: s3admin
      DARKANGEL_S3_SECRET_KEY: s3admin-secret
      DARKANGEL_S3_SECURE: "false"
      DARKANGEL_DATABASE_URL: postgresql+psycopg://darkangel:darkangel@localhost:5432/darkangel
```

- Replace the `Start MinIO` and `Wait for MinIO` steps with:

```yaml
      - name: Start SeaweedFS
        # A plain container, not a service container: those cannot pass the
        # `server -s3` command. Same release and digest as the Infra stack.
        run: |
          docker run -d --name s3 -p 8333:8333 \
            -e AWS_ACCESS_KEY_ID=s3admin \
            -e AWS_SECRET_ACCESS_KEY=s3admin-secret \
            chrislusf/seaweedfs:4.47@sha256:ce9e796f1fe6f06968f4c04bdaf8f678dad9c8acdfef3d244133d71bfa6bf882 \
            server -ip.bind=0.0.0.0 -s3 -s3.port.iceberg=0 -s3.port.lance=0

      - name: Wait for SeaweedFS
        run: |
          for _ in $(seq 1 30); do
            if curl -fsS http://localhost:8333/healthz >/dev/null; then
              echo "SeaweedFS is ready"
              exit 0
            fi
            sleep 2
          done
          echo "SeaweedFS never became ready" >&2
          docker logs s3 >&2
          exit 1
```

- Summary line: `"Ran against MinIO at ..."` → `"Ran against SeaweedFS at ..."`
- `name: MinIO logs on failure` / `run: docker logs minio` → `name: SeaweedFS logs on failure` / `run: docker logs s3`

Check: `git grep -n -i minio -- .github/workflows/ci.yml docker-compose.test.yml` → no output.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.test.yml backend/tests/integration/conftest.py .github/workflows/ci.yml Makefile
git commit -m "test: run the integration suite and CI against SeaweedFS 4.47"
```

(CI itself is proven when the branch's PR runs; Task 6 opens it.)

---

### Task 2: Neutral names and production defaults

**Files:**
- Modify: `backend/app/core/config.py:27-32`
- Modify: `backend/.env.example:11-19`
- Modify: `backend/pyproject.toml:49` (integration marker text)
- Modify: `backend/app/api/routes/files.py` (function name, comments)
- Modify: `backend/app/scripts/backfill.py` (import, comments, `mc mirror` hint)
- Modify: `backend/app/repositories/files.py` (comments)
- Modify: `backend/tests/conftest.py` (`FakeMinio`, `store` fixture)
- Modify: `backend/tests/integration/conftest.py` (fixture name, comments, skip reason)
- Rename: `backend/tests/integration/test_files_minio.py` → `backend/tests/integration/test_files_s3.py`
- Modify: `backend/tests/integration/test_backfill.py`, `test_phase2_acceptance.py`, `test_repository.py`
- Modify: `backend/tests/unit/test_files.py`, `backend/tests/regression/test_pr12_unsafe_file_names.py`, `backend/tests/regression/test_r1_cross_owner_isolation.py`
- Regenerate: `backend/tests/regression/openapi.snapshot.json`

**Interfaces:**
- Consumes: Task 1's SeaweedFS for the integration run.
- Produces: `app.api.routes.files.s3_client() -> Minio` (lru_cached, same body as before); `tests.conftest.FakeS3` (same methods as `FakeMinio`); pytest fixtures `store` (unchanged name) and `s3_bucket` (session, autouse, yields the bucket name).

- [ ] **Step 1: Mechanical renames**

```bash
cd backend
git mv tests/integration/test_files_minio.py tests/integration/test_files_s3.py
grep -rl -e minio_client -e FakeMinio -e minio_bucket app tests \
  | xargs sed -i '' -e 's/minio_client/s3_client/g' -e 's/FakeMinio/FakeS3/g' -e 's/minio_bucket/s3_bucket/g'
```

(On Linux use `sed -i` without `''`.) Then rename the test `test_minio_issues_a_real_version_id` → `test_the_store_issues_a_real_version_id` in `tests/integration/test_files_s3.py`.

- [ ] **Step 2: Production defaults**

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

`backend/.env.example`, the storage block:

```sh
# Home files in the Infra SeaweedFS (defaults shown). `s3:8333` only resolves on
# infra-net; from a laptop use DARKANGEL_S3_ENDPOINT=s3.infra.famillelallier.net
# with DARKANGEL_S3_SECURE=true (and the same SSL_CERT_FILE). The secret is
# .portainer.env's S3_SECRET_KEY.
# DARKANGEL_S3_ENDPOINT=s3:8333
# DARKANGEL_S3_SECURE=false
# DARKANGEL_S3_BUCKET=darkangel-files
# DARKANGEL_S3_ACCESS_KEY=darkangel
# DARKANGEL_S3_SECRET_KEY=
```

`backend/pyproject.toml` marker: `"integration: needs real S3 storage + Postgres (see docs/testing.md)"`.

- [ ] **Step 3: Reword the remaining mentions**

Run: `git grep -n -i minio -- backend | grep -v -E 'from minio|import Minio|Minio\(|-> Minio|"minio>='`

For each hit, replace "MinIO" with "object storage" (prose about the role) or "SeaweedFS" (prose about the concrete service), keeping the sentence otherwise intact. Specific ones:
- `tests/integration/conftest.py`: skip reason `f"MinIO unreachable at ..."` → `f"S3 store unreachable at ..."`; comment `(provision-minio.sh)` → `(Infra's s3-provision versioned=1)`; `` `make minio` does `` → `` Infra's `make s3-provision` does ``.
- `tests/conftest.py`: `"""The slice of minio.Minio ..."""` stays (it names the SDK class); `store` docstring → `"""Swap object storage for FakeS3. Explicit, never autouse: the integration suite must keep talking to the real service."""`.
- `app/scripts/backfill.py` warning text: `` (`mc mirror --preserve infra/darkangel-files ./backup`) `` → `` (e.g. `aws s3 sync s3://darkangel-files ./backup --endpoint-url https://s3.infra.famillelallier.net`) ``.
- `tests/unit/test_files.py:493`: `pytest.fail("PATCH touched MinIO")` → `pytest.fail("PATCH touched object storage")`.
- `app/api/routes/files.py:291` docstring `no MinIO\ncall` → `no storage\ncall`.

Re-run the grep. Expected: no output.

- [ ] **Step 4: Regenerate the OpenAPI snapshot** (the docstring change above is in it)

Run: `make snapshot && git diff --stat backend/tests/regression/openapi.snapshot.json`
Expected: exactly one line changed in the snapshot (the PATCH description).

- [ ] **Step 5: Run every backend suite**

Run: `make lint-backend && make test-unit && make test-regression && make services-test-up && make test-integration`
Expected: all pass; integration 0 skipped.

- [ ] **Step 6: Commit**

```bash
git add -A backend
git commit -m "refactor: neutral S3 names; default to the Infra SeaweedFS (s3:8333, darkangel)"
```

---

### Task 3: A missing version id fails the upload

**Files:**
- Modify: `backend/app/api/routes/files.py` (logger, `_unversioned`, both upload paths)
- Test: `backend/tests/unit/test_files.py`

**Interfaces:**
- Consumes: `s3_client()`, `FakeS3`, fixtures `repo` / `store` from Task 2.
- Produces: `_unversioned() -> HTTPException` (private to `files.py`).

- [ ] **Step 1: Write the failing tests**

Add `from types import SimpleNamespace` to the imports of `backend/tests/unit/test_files.py`, then after `test_a_failed_upload_leaves_no_reservation`:

```python
def unversioned(store, monkeypatch):
    """Make the fake behave like an unversioned bucket: the bytes land, but the
    write result carries no version id."""
    real_put = store.put_object

    def put_object(*args, **kwargs):
        real_put(*args, **kwargs)
        return SimpleNamespace(version_id=None)

    monkeypatch.setattr(store, "put_object", put_object)


def test_an_unversioned_bucket_fails_a_new_upload(repo, store, monkeypatch, caplog):
    unversioned(store, monkeypatch)

    response = upload()

    assert response.status_code == 502
    assert "no version id" in caplog.text
    assert repo.rows == []
    assert store.objects == {}


def test_an_unversioned_bucket_fails_a_new_version(repo, store, monkeypatch):
    first = upload(name="notes.txt", data=b"one").json()
    unversioned(store, monkeypatch)

    response = upload(name="notes.txt", data=b"two now")

    assert response.status_code == 502
    assert repo.versions[uuid.UUID(first["id"])] == 1
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -k unversioned -v`
Expected: both FAIL — the first with `201 != 502`, the second with `200 != 502`.

- [ ] **Step 3: Implement**

In `backend/app/api/routes/files.py`, add `import logging` to the stdlib imports and, after the imports:

```python
log = logging.getLogger(__name__)
```

Next to `s3_client()`:

```python
def _unversioned() -> HTTPException:
    """Every write must come back with a version id: the version table indexes
    them and restore (BR-8) replays them. An unversioned bucket returns none."""
    log.error(
        "bucket %s returned no version id; is versioning enabled?", get_settings().s3_bucket
    )
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "Storage is unavailable")
```

In the new-file path, replace
`repo.finalize(row, s3_version_id=written.version_id or "", actor_sub=claims["sub"])` with:

```python
    if not written.version_id:
        repo.abandon(row)
        with suppress(Exception):
            s3_client().remove_object(settings.s3_bucket, row.object_key)
        raise _unversioned()
    repo.finalize(row, s3_version_id=written.version_id, actor_sub=claims["sub"])
```

In `_append_version`, before `repo.add_version(`:

```python
    if not written.version_id:
        # The previous bytes are already overwritten on an unversioned bucket;
        # failing loudly is all that is left to do.
        raise _unversioned()
```

and change `s3_version_id=written.version_id or "",` to `s3_version_id=written.version_id,`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v`
Expected: all PASS, including the two new ones.

- [ ] **Step 5: Full backend check**

Run: `make lint-backend && make test-unit && make test-regression && make test-integration`
Expected: all pass (integration proves SeaweedFS always returns an id on the versioned test bucket, so the new branch never fires there).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/routes/files.py backend/tests/unit/test_files.py
git commit -m "fix(files): refuse an upload the store returns no version id for"
```

---

### Task 4: Deploy plumbing — `S3_SECRET_KEY`, provisioning moves to Infra

**Files:**
- Modify: `deploy/portainer-stack.yml:5-12,42-49`
- Modify: `.portainer.env.example:19-22`
- Modify: `scripts/portainer-stack.sh:59-65,84,201-205`
- Modify: `.github/workflows/deploy.yml:96-102`
- Delete: `scripts/provision-minio.sh`
- Modify: `Makefile:42,254-255`
- Modify: `scripts/provision-postgres.sh:8-10`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: stack variable `S3_SECRET_KEY`; `stack_env <owner> <tag> <s3-secret-key> <postgres-password>` emitting `{name:"S3_SECRET_KEY",...}`.

- [ ] **Step 1: Update the self-test expectation first**

`scripts/portainer-stack.sh:84`, in `want=`, replace `"MINIO_SECRET_KEY"` with `"S3_SECRET_KEY"`.

Run: `make stack-selftest`
Expected: FAIL (the helper still emits `MINIO_SECRET_KEY`).

- [ ] **Step 2: Rename in `portainer-stack.sh`**

- Line 59 comment: `the MinIO and Postgres secrets` → `the S3 and Postgres secrets`.
- Line 61: `stack_env() { # <image-owner> <image-tag> <s3-secret-key> <postgres-password>`
- Line 62: `--arg minio "$3"` → `--arg s3 "$3"`
- Line 65: `{name: "S3_SECRET_KEY", value: $s3},`
- Lines 201-205:

```bash
    [ -n "${S3_SECRET_KEY:-}" ] && [ "$S3_SECRET_KEY" != change-me ] ||
      note "S3_SECRET_KEY is not set in .portainer.env: the Files page will fail (see README, Files)"
```

and `"${MINIO_SECRET_KEY:-}"` → `"${S3_SECRET_KEY:-}"` in the `stack_env` call.

Run: `make stack-selftest`
Expected: PASS.

- [ ] **Step 3: Stack file, env example, deploy workflow**

`deploy/portainer-stack.yml` header line:
`#   S3_SECRET_KEY     secret of S3 identity darkangel (Infra: make s3-provision)`

The storage comment and variable:

```yaml
      # Home files go to the Infra SeaweedFS (`s3:8333` on infra-net), bucket
      # darkangel-files as identity darkangel -- both made by Infra's
      # `make s3-provision app=darkangel bucket=darkangel-files versioned=1`.
      # S3_SECRET_KEY is a stack variable `make up` copies from .portainer.env.
      DARKANGEL_S3_SECRET_KEY: ${S3_SECRET_KEY:-}
```

and in the Postgres comment below it, `the same way MINIO_SECRET_KEY arrives` → `the same way S3_SECRET_KEY arrives`.

`.portainer.env.example`:

```sh
# Secret key of the S3 identity darkangel (bucket darkangel-files) in the Infra
# SeaweedFS, which the API stores home files with. The same value as
# DARKANGEL_S3_SECRET_KEY in the Infra .env, applied there by
# `make s3-provision app=darkangel bucket=darkangel-files versioned=1`.
# `make up` hands it to the stack -- rerun it after a change.
S3_SECRET_KEY=change-me
```

`.github/workflows/deploy.yml`:

```yaml
          # The API path rewrites the stack's whole Env list on every deploy,
          # so the S3 secret has to be sent with it -- left out, each run
          # would blank it and the Files page would stop working. The same
          # value as DARKANGEL_S3_SECRET_KEY in the Infra .env.
          S3_SECRET_KEY: ${{ secrets.S3_SECRET_KEY }}
```

- [ ] **Step 4: Remove MinIO provisioning**

```bash
git rm scripts/provision-minio.sh
```

`Makefile`: delete the two-line `minio:` target (lines 254-255) and remove `minio` from the `.PHONY` list on line 42.

`scripts/provision-postgres.sh` header: `# the generated password back into .portainer.env as POSTGRES_PASSWORD, the` / `# same way provision-minio.sh handles MINIO_SECRET_KEY.` → `# the generated password back into .portainer.env as POSTGRES_PASSWORD.`

- [ ] **Step 5: Check**

Run: `make stack-selftest && make help | grep -c '^ *minio' ; git grep -n -i minio -- deploy scripts .portainer.env.example .github/workflows/deploy.yml Makefile`
Expected: selftest PASS; count `0`; grep prints nothing.

- [ ] **Step 6: Commit**

```bash
git add -A deploy scripts .portainer.env.example .github/workflows/deploy.yml Makefile
git commit -m "chore(deploy): S3_SECRET_KEY; provisioning moves to Infra's make s3-provision"
```

---

### Task 5: `make files-reset` for the cutover

**Files:**
- Create: `scripts/files-reset.sh`
- Modify: `Makefile` (`.PHONY` line 42, new target next to `postgres:`)

**Interfaces:**
- Consumes: the `files` / `file_versions` tables (Alembic head); `PGADMIN_URL` convention from `scripts/provision-postgres.sh`.
- Produces: `make files-reset` — prints counts; empties both tables only when `CONFIRM=darkangel` (or `CONFIRM=$DB`).

- [ ] **Step 1: Write the script**

`scripts/files-reset.sh` (then `chmod +x scripts/files-reset.sh`):

```bash
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
```

`Makefile`: add `files-reset` to the `.PHONY` list on line 42, and after the `postgres:` target:

```make
files-reset: ## One-time, after the SeaweedFS cutover: empty files + file_versions (PGADMIN_URL, CONFIRM=darkangel)
	@scripts/files-reset.sh
```

- [ ] **Step 2: Try it against the test Postgres, with rows in it**

```bash
make services-test-up
export PGADMIN_URL=postgresql://darkangel:darkangel@localhost:5432/postgres
DARKANGEL_DATABASE_URL=postgresql+psycopg://darkangel:darkangel@localhost:5432/darkangel make migrate
psql "$PGADMIN_URL" -q -c '\connect darkangel' -c "
  INSERT INTO files (id, owner_sub, name, object_key, size_bytes, content_type, status)
  VALUES (gen_random_uuid(), 'probe', 'a.txt', 'probe/a', 1, 'text/plain', 'ready');"
make files-reset; echo "exit=$?"
CONFIRM=darkangel make files-reset
make files-reset; echo "exit=$?"
```

Expected, in order: `darkangel holds 1 files, 0 versions` then the refusal and a non-zero exit; `emptied files and file_versions in darkangel`; `darkangel holds 0 files, 0 versions` and a non-zero exit.

Then `make services-test-down`.

- [ ] **Step 3: Commit**

```bash
git add scripts/files-reset.sh Makefile
git commit -m "chore: make files-reset, the one-time SeaweedFS cutover step"
```

---

### Task 6: Docs, final sweep, PR

**Files:**
- Modify: `README.md` (Files section ~122-140, bootstrap step 5 ~198, Repository secrets ~219)
- Modify: `CLAUDE.md:23,53-55`
- Modify: `docs/testing.md` (every MinIO mention)
- Modify: `docs/files-feature.md` (every MinIO mention)
- Modify: `frontend/tests/regression/pr12-download-blob-url.test.ts:17` (comment)

**Interfaces:**
- Consumes: names from Tasks 1-5 (`s3_client`, `FakeS3`, `s3_bucket`, `S3_SECRET_KEY`, `make files-reset`, port 8333).
- Produces: the PR.

- [ ] **Step 1: README**

The Files section's first paragraph and setup paragraph become:

```markdown
The **Files** page (`/files`) keeps your home files in the Infra SeaweedFS. Each
signed-in user sees only their own: the API (`/api/files`) stores them in bucket
`darkangel-files`, as the S3 identity `darkangel`, over `http://s3:8333` on
`infra-net`. The browser never talks to object storage directly. Uploads are
capped at 100 MB by the Infra NGINX (`client_max_body_size` in
`deploy/nginx/darkangel.conf`). The bucket is versioned: the API refuses an
upload the store returns no version id for.

**One-time setup:** on the Docker host, in the Infra repo, set
`DARKANGEL_S3_SECRET_KEY` in its `.env` and run
`make s3-provision app=darkangel bucket=darkangel-files versioned=1` (the
`versioned=1` matters). Put the same value in DarkAngel's `.portainer.env` as
`S3_SECRET_KEY` and in the `S3_SECRET_KEY` repository secret, then `make up`.
To rotate it, change it in all three places and run both targets again.

**Moving from MinIO (once):** Infra started SeaweedFS empty. After the first
deploy on SeaweedFS, run `PGADMIN_URL=... CONFIRM=darkangel make files-reset` to
drop the metadata of files whose bytes stayed behind, then upload a file and
download it back.
```

Bootstrap step 5:

```markdown
5. **Provision file storage** — run Infra's `make s3-provision app=darkangel
   bucket=darkangel-files versioned=1` and set `S3_SECRET_KEY` in
   `.portainer.env` (see [Files](#files)).
```

Repository secrets bullet:

```markdown
- `S3_SECRET_KEY` — the secret of the `darkangel` S3 identity, the same value as
  `DARKANGEL_S3_SECRET_KEY` in the Infra `.env` and `S3_SECRET_KEY` in
  `.portainer.env`. The API path rewrites the
```

(keep the rest of that bullet's sentence as it is). Reword any other README MinIO mention the same way.

- [ ] **Step 2: CLAUDE.md**

Line 23: `# needs a real MinIO` → `# needs SeaweedFS + Postgres`.
Lines 53-55:

```markdown
  bytes in the Infra SeaweedFS (bucket `darkangel-files`, provisioned by Infra's
  `make s3-provision`) under `<sub>/<file uuid>`. `api/routes/folders.py` +
  `repositories/folders.py` hold the folder tree. Every query filters on the
  caller's `sub`. Tests swap `s3_client()`
```

- [ ] **Step 3: `docs/testing.md` and `docs/files-feature.md`**

`docs/testing.md`: `FakeMinio` → `FakeS3`; `minio_bucket` → `s3_bucket`; `files.minio_client()` → `files.s3_client()`; `test_files_minio.py` → `test_files_s3.py`; `localhost:9000` → `localhost:8333`; the example skip line → `65 tests  Skipped: S3 store unreachable at localhost:8333: HTTPConnectionPool …`; `` `make minio` does `` → `` Infra's `make s3-provision` does ``; troubleshooting `MinIO never became ready` → `SeaweedFS never became ready`, `docker logs minio` → `docker logs s3`; remaining "a real MinIO" → "a real SeaweedFS".

`docs/files-feature.md`: it describes the live design, so replace "MinIO" with "SeaweedFS" (the service) or "object storage" (the role) throughout, `minio:9000` → `s3:8333`, `scripts/provision-minio.sh` / `make minio` → Infra's `make s3-provision ... versioned=1`. The paragraph at ~391-396 about amending `provision-minio.sh`'s policy for Phase 3 becomes: "Phase 3 reads object versions: confirm the `darkangel` identity's `Read,Write,List,Tagging` actions cover `GetObjectVersion` and `ListBucketVersions` on the Infra SeaweedFS before starting it."

Frontend comment: `Regression — PR #12, MinIO home files.` → `Regression — PR #12, home files.`

- [ ] **Step 4: Final sweep**

Run:
```bash
git grep -n -i minio -- ':!docs/superpowers' | grep -v -E 'from minio|import Minio|Minio\(|-> Minio|"minio>=|slice of minio\.Minio'
```
Expected: no output. Any hit: reword it as in Task 2 Step 3.

- [ ] **Step 5: Full verification**

Run: `make services-test-up && make verify && make test-integration && make stack-selftest`
Expected: `verify: ok`, integration all pass with 0 skipped, selftest PASS.

- [ ] **Step 6: Commit, push, open the PR**

```bash
git add -A README.md CLAUDE.md docs/testing.md docs/files-feature.md frontend/tests/regression/pr12-download-blob-url.test.ts
git commit -m "docs: SeaweedFS replaces MinIO"
git push
gh pr create --draft --title "Move home files from MinIO to the Infra SeaweedFS" --body-file - <<'EOF'
Follows Infra PR #70. Spec: docs/superpowers/specs/2026-09-27-minio-to-seaweedfs-design.md

**Before merging** (the backend dials `s3:8333` as soon as this deploys):
1. Infra: `make s3-provision app=darkangel bucket=darkangel-files versioned=1`
2. `gh secret set S3_SECRET_KEY` (same value as Infra's `DARKANGEL_S3_SECRET_KEY`) and `.portainer.env`

**After the deploy:** `PGADMIN_URL=... CONFIRM=darkangel make files-reset`, then upload + download a file and check its `file_versions.s3_version_id` is non-empty.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
```

Expected: the PR's `backend-integration` job goes green against SeaweedFS.
