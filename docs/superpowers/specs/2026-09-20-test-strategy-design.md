# Test strategy: unit, integration and regression suites in CI

Date: 2026-09-20 · Status: approved design, awaiting spec review

## Goal

Give DarkAngel a complete, documented test suite (unit, integration, regression) on both
the FastAPI backend and the Vue frontend, and make every suite a required check in
GitHub Actions.

## Current state (`main` at bc83154)

- Backend: `backend/tests/{conftest,test_auth,test_files,test_health}.py`, all in-process.
  MinIO is a `FakeMinio` monkeypatched over `files.minio_client`; Keycloak is a fake JWKS
  (`conftest.fake_jwks`, tokens from `conftest.token()`).
- `pytest-cov` is a dev dependency and `make coverage` enforces `COVERAGE_MIN=80`, but CI
  never runs it.
- Frontend: no test tooling. CI only runs `vue-tsc -b && vite build`.
- CI (`.github/workflows/ci.yml`): a `backend` job (ruff + pytest) and a `frontend` job
  (build). No separation of test types, no coverage gate, no artifacts. No test docs.

## Decisions

| Question | Decision |
|---|---|
| What is an integration test? | The real FastAPI app against **real MinIO** over HTTP. Auth stays mocked: locally signed JWTs and the fake JWKS. No Keycloak in CI. |
| Frontend scope | Vitest unit + component tests. No Playwright E2E. |
| What is a regression test? | (a) Tests that pin a fixed bug, named after the issue/PR. (b) An OpenAPI contract snapshot that fails on unintended API changes. |
| CI shape | One job per suite, running in parallel. |

## Design

### 1. Backend layout

```
backend/tests/
  conftest.py            # shared: fake_jwks, token(), marker-by-directory hook
  unit/                  # existing test_auth/test_files/test_health move here
  integration/
    conftest.py          # real MinIO client + bucket fixture
    test_files_minio.py
  regression/
    test_openapi_contract.py
    openapi.snapshot.json
    test_<issue>_<slug>.py   # one file per pinned bug
```

- `pyproject.toml` registers markers `unit`, `integration`, `regression` and sets
  `--strict-markers`. `conftest.py` applies the marker from the test's directory
  (`pytest_collection_modifyitems`), so tests never need a decorator.
- Unit tests keep `FakeMinio` and the fake JWKS. `FakeMinio` moves to
  `tests/unit/` (it is only a unit-level double); `conftest.py` keeps `fake_jwks` and
  `token()`.
- Test-support imports use `tests.conftest` as today.

### 2. Integration suite

- Config comes from the existing settings: `DARKANGEL_S3_ENDPOINT`, `_S3_ACCESS_KEY`,
  `_S3_SECRET_KEY`, `_S3_BUCKET`, `_S3_SECURE=false`. The app does **not** create its
  bucket (`make minio` does), so the integration fixture creates a uniquely named bucket
  per session, points `get_settings()`/`minio_client()` at it (clearing their
  `lru_cache`), and deletes every object and the bucket on teardown.
- Reachability: if MinIO is unreachable the whole directory is **skipped locally** with a
  clear reason, but **fails** when `CI=true`, so a broken service can never turn CI green
  by skipping.
- Cases (through `TestClient`, real S3 underneath):
  - upload → list → download → delete round trip with byte-exact content and content type
  - two users (`sub` claims) cannot see or fetch each other's files
  - unicode and space-containing file names round-trip
  - a multi-part-sized upload (larger than the 10 MiB part size used by `put_object`)
  - overwriting a name replaces the object
  - downloading or deleting a missing file returns the same status as the unit suite
  - unauthenticated requests are rejected before touching MinIO
- Local run: `docker-compose.test.yml` (MinIO only) plus `make test-integration`.

### 3. Regression suite

- `tests/regression/test_<issue>_<slug>.py`, each with a docstring linking the issue/PR
  and describing the original failure. Initial set, taken from history: unsafe file names
  (`..`, backslash, 256+ chars), token failure modes (expired, wrong audience, wrong
  issuer, no token), `/api/health` staying public. These are *moved* out of the unit
  files, not duplicated, so each check lives in exactly one suite.
- `test_openapi_contract.py` compares `app.openapi()` to `openapi.snapshot.json`
  (sorted-key JSON). The failure message tells the reader to run `make snapshot` if the
  change is intended. The snapshot diff is then reviewed in the PR.
- Frontend: `frontend/tests/regression/`, seeded with the Files download blob-URL revoke
  timing fix.

### 4. Frontend suite

- Dev dependencies: `vitest`, `@vue/test-utils`, `jsdom`, `@vitest/coverage-v8`.
  Config lives in `vite.config.ts` (`test` block: jsdom, `@/` alias already resolved by
  Vite, coverage reporters `text` + `lcov`, thresholds set once a baseline exists).
- Scripts: `test`, `test:coverage`. Layout `frontend/tests/{unit,regression}/`.
- Unit/component targets: `api/client` (base URL, bearer token attach, non-2xx error
  mapping), `stores/{files,health,me}` (loading/error/data transitions, `fetch` mocked),
  `auth.ts` (`oidc-client-ts` mocked: login redirect, callback, logout), router guard
  (unauthenticated redirect), `FilesView` (list render, upload, delete, download).
- `tsconfig` must include the tests so `vue-tsc -b` type-checks them.

### 5. Makefile

New targets, all using the existing `$(PY)` / `npm` conventions:

| Target | Runs |
|---|---|
| `test-unit` | `pytest -m unit` |
| `test-integration` | `pytest -m integration` (needs MinIO) |
| `test-regression` | `pytest -m regression` |
| `test-frontend` | `npm run test` |
| `snapshot` | regenerate `openapi.snapshot.json` |
| `test` | unit, integration, regression and frontend in one run; it never starts MinIO itself, so integration skips locally if MinIO is down |
| `coverage` | backend unit+integration with `--cov-fail-under=$(COVERAGE_MIN)`, frontend `test:coverage` |

`verify` and `ci` already depend on `test`, so they inherit the new suites.

### 6. CI (`ci.yml`)

Triggers unchanged (`push` on all branches, `pull_request`), same concurrency group.

| Job | Steps |
|---|---|
| `backend-unit` | install → ruff → `pytest -m "unit or regression"` with coverage |
| `backend-integration` | install → start MinIO → wait until ready → `pytest -m integration` with `CI=true` |
| `frontend` | `npm ci` → `vue-tsc` → `vitest run --coverage` → `vite build` |

- MinIO is started with `docker run -d -p 9000:9000 minio/minio server /data` plus a
  readiness loop on `/minio/health/ready`. GitHub service containers cannot pass the
  `server /data` command, so a plain step is used.
- Each job uploads JUnit XML and coverage as artifacts and appends a summary to
  `$GITHUB_STEP_SUMMARY`.
- Coverage gate: backend combined `COVERAGE_MIN` (80). Frontend threshold is set after the
  first measured baseline, never below it.
- `deploy.yml` is untouched. It has a hard rule against `pull_request` triggers
  (self-hosted runner holds the Docker socket), so the new jobs are added to `ci.yml`
  only, which runs on GitHub-hosted `ubuntu-latest`.

### 7. Documentation

- `docs/testing.md`: the layers and what each is for, directory and marker layout, how to
  run each suite locally, CI job mapping, how to write and name a regression test, how to
  update the OpenAPI snapshot, how to debug a failing CI job.
- README links to it; CLAUDE.md "Commands" gains the new targets.

## Error handling and edge cases

- Integration cleanup runs in a `finally`/fixture teardown so a failed test cannot leak
  objects or buckets into the shared MinIO.
- Snapshot comparisons use sorted-key JSON so ordering churn does not cause false
  failures.
- The integration skip-vs-fail switch keys on `CI=true` (set by GitHub Actions).

## Out of scope

Playwright E2E, real Keycloak in CI, mutation testing, deploy gated on CI, load tests.

## Acceptance criteria

1. `make test` passes locally with MinIO up; integration tests skip cleanly without it.
2. Backend coverage ≥ 80% and a frontend coverage report is produced.
3. Changing a route's response shape without `make snapshot` fails the regression job.
4. A push or PR shows three separate passing jobs: `backend-unit`, `backend-integration`,
   `frontend`.
5. Breaking the MinIO service in CI fails `backend-integration` (does not skip).
6. `docs/testing.md` exists and is linked from the README.
