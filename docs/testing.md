# Testing

Three backend suites and two frontend ones. Which suite a test belongs to is
decided by the directory it sits in — there are no marker decorators to
remember and none to forget.

## The layers

| Suite | Where | What is real | CI job |
|---|---|---|---|
| Backend unit | `backend/tests/unit/` (41 tests) | Nothing outside the process. MinIO is `FakeMinio`, the database is `FakeFileRepository`, Keycloak is a fake JWKS. | `backend-unit` |
| Backend regression | `backend/tests/regression/` (23 tests) | Same as unit. Each file pins one fixed bug, or the API contract. | `backend-unit` |
| Backend integration | `backend/tests/integration/` (45 tests) | A real MinIO in a throwaway bucket, and a real PostgreSQL migrated to head. Auth stays faked. | `backend-integration` |
| Frontend unit | `frontend/tests/unit/` | jsdom. `fetch` and `oidc-client-ts` are mocked. | `frontend` |
| Frontend regression | `frontend/tests/regression/` | Same as frontend unit. | `frontend` |

Frontend unit + regression together are 37 tests across 8 files (Vitest 5,
`frontend/vite.config.ts`'s `test` block).

Route logic is unit-tested against `FakeFileRepository` and the real SQL behind
it is exercised only by the integration suite. That split is deliberate: the
`backend-unit` CI job measures coverage over `-m "unit or regression"` alone,
because it has no services, so anything that only a real database can run is
invisible to that gate. `make coverage-backend` does append all three suites
and sees the whole picture.

Keycloak is never real, not even in integration: the suite signs its own RS256
tokens against a key it generates, and `backend/tests/conftest.py`'s
`fake_jwks` fixture (autouse, everywhere) swaps the JWKS client out for one
that returns that key. Only storage is real, and only in the integration
suite.

## Running them

```sh
make test              # every suite; integration skips if the services are down
make test-unit         # backend unit only, no services needed
make test-regression   # backend regression only
make test-integration  # backend integration; needs MinIO + Postgres
make test-frontend     # vitest
make coverage          # both sides; the backend fails under 80%
```

Integration needs a MinIO on `localhost:9000` and a PostgreSQL on
`localhost:5432`:

```sh
make services-test-up   # docker-compose.test.yml: MinIO + Postgres
make test-integration
make services-test-down # also drops their data
```

Narrow a run with `ARGS`:

```sh
make test-unit ARGS="-k token -v"
make test-backend ARGS="tests/regression/test_pr10_token_rejection.py"
cd frontend && npm run test tests/unit/router.test.ts
```

`test-backend` is a lower-level escape hatch: an unfiltered `pytest $(ARGS)`,
with no `-m` marker applied. Reach for the suite-specific targets above first;
use `test-backend` when you want to target one file or node id directly.

## Reading the output

Every backend run ends with up to three blocks, printed by
`pytest_terminal_summary` in `backend/tests/conftest.py`:

```
--------------------------------- modules run ----------------------------------
  tests/unit/test_auth.py                               5 tests
  tests/unit/test_files.py                             35 tests
  tests/unit/test_health.py                             1 test
------------------------------ skipped at runtime ------------------------------
   45 tests  Skipped: MinIO unreachable at localhost:9000: HTTPConnectionPool …
------------------- deselected by -m (not run in this pass) --------------------
  integration                                          45 tests   -> make test-integration
  regression                                           23 tests   -> make test-regression
```

- **modules run** — one line per test module that actually executed, with how
  many of its tests did. This is the answer to "what did this pass cover".
- **skipped at runtime** — collected but skipped, one line per distinct
  *reason*, not per test: every integration test skips over the same
  unreachable MinIO, and that is one line. `-rs` prints them per test.
- **deselected by -m** — what the marker filter removed, grouped by suite,
  each with the target that would run it. pytest's own summary gives only a
  total ("23 deselected"), which looks the same whether one suite was left out
  or two.

`--strict-markers -rfE` is in `addopts`, so failures and errors are recapped
as a list at the end of a long run. Deliberately not `-ra`: that repeats a
skip reason once per test.

The frontend reporter is `verbose` (`frontend/vite.config.ts`), so vitest
names all 37 tests instead of collapsing a green run to `8 passed (8)`.

## How the markers work

`backend/tests/conftest.py` has a `pytest_collection_modifyitems` hook that
marks each test with the name of its parent directory. `backend/pyproject.toml`
registers `unit`, `integration` and `regression` and turns on
`--strict-markers`, so an unregistered `@pytest.mark.x` decorator is an error
instead of a silent no-op. (`--strict-markers` does not check `-m` expressions:
a typo there just deselects everything — pytest exits 5, "no tests collected",
rather than reporting the marker name is wrong, so read the summary line, not
just the exit code, when a run looks suspiciously empty.)

Putting a test file in the right directory is the entire contract. No test in
this repository carries a marker decorator. A test in `tests/unit/` must never
touch the network or a service.

## The integration services

Two session fixtures in `backend/tests/integration/conftest.py` own the real
services: `minio_bucket` and `pg_database`. They share a shape — default the
environment the app reads, clear the `lru_cache`s that already read it, and
fail rather than skip when `CI=true`.

### The bucket

The app does not create its own bucket — in production `make minio` does. So
`backend/tests/integration/conftest.py` creates one per session, named
`darkangel-test-<random>`, and removes every object and the bucket itself in
teardown, even after a failure.

It points the app at that bucket by setting `DARKANGEL_S3_*` in the environment
and then clearing the `lru_cache` on both `get_settings()` and
`files.minio_client()` — without that, the app keeps whatever it read at
import.

Exporting any `DARKANGEL_S3_*` variable before the run targets a different
MinIO instead of the compose one (the fixture only fills in values that are
still unset).

### The database

`pg_database` defaults `DARKANGEL_DATABASE_URL` to
`postgresql+psycopg://darkangel:darkangel@localhost:5432/darkangel`, clears
`get_settings()` plus `db.engine()` and `db.session_factory()` — an engine
built before the URL was set would point at the unreachable production host
for the rest of the session — and then runs `alembic upgrade head` itself.
Nothing in CI migrates separately; the fixture is the migration step.

The per-test `db` fixture truncates between tests:
`TRUNCATE audit_log, file_versions, files, folders RESTART IDENTITY CASCADE`.
TRUNCATE, not DELETE, because `audit_log` carries a `BEFORE DELETE` trigger
that makes it append-only and TRUNCATE does not fire row triggers.

As with the bucket, exporting `DARKANGEL_DATABASE_URL` before the run targets
a different database instead of the compose one.

The suite also shadows the shared `store` and `repo` fixtures with ones that
fail on sight: an integration test that asked for `FakeMinio` or
`FakeFileRepository` would quietly stop being an integration test.

**An unreachable service skips locally and fails in CI.** The switch is the
`CI` environment variable, which GitHub Actions sets to `true` for the
`backend-integration` job, and both fixtures read it. A broken service must
never turn a CI run green by skipping.

The restore (env vars + both `lru_cache`s) runs at *session* teardown, after
every test in the process has already run. That is why integration always
runs in its own pytest process, separate from unit and regression — a shared
process would leave the unit or regression tests reading the integration
suite's throwaway bucket settings. `make test` and `coverage-backend` run all
three suites as three separate processes; CI runs unit+regression together as
one process (`pytest -m "unit or regression"`) and integration in a second.
The invariant that matters is only that integration never shares a process
with the others.

## Writing a regression test

One file per fixed bug, named `test_<issue-or-pr>_<slug>.py` on the backend
(e.g. `test_pr10_token_rejection.py`, `test_pr12_unsafe_file_names.py`) and
`<issue-or-pr>-<slug>.test.ts` on the frontend (e.g.
`pr12-download-blob-url.test.ts`). The docstring or header comment says which
issue or PR, and what the original failure looked like — the point is that
someone reading the test in two years knows why it exists.

Move the check out of wherever it was; never leave a copy behind. A regression
check pins one fixed bug and lives in exactly one suite — never copy it into a
second. That is different from the unit/integration layering: the same
scenario may legitimately appear at both layers, once against `FakeMinio` and
once against a real MinIO, because each proves something the other cannot.

Before trusting a new regression test, reintroduce the bug and watch it fail.

## The OpenAPI snapshot

`backend/tests/regression/test_openapi_contract.py` compares `app.openapi()`
to `backend/tests/regression/openapi.snapshot.json`. Any change to a path, a
status code or a response model fails it.

When the change is intended:

```sh
make snapshot
```

That rewrites the snapshot, and the diff is then reviewed in the pull request —
which is the whole point: an API change is visible in review instead of silent.

## CI jobs

`.github/workflows/ci.yml` runs three jobs in parallel: `backend-unit` (lint +
unit + regression + coverage gate), `backend-integration` (starts a real MinIO
container and a `postgres:16-alpine` service container, then the integration
suite), and `frontend` (vitest with coverage,
then `npm run build`, which type-checks with `vue-tsc` first). Each uploads its
JUnit XML as a workflow artifact — `backend-unit` also uploads `coverage.xml`
(the only job with a `--cov` flag; `backend-integration` has none), and
`frontend` also uploads its `lcov.info`.

## Debugging a failing CI job

- **`backend-unit` red on a test** — reproduce locally with
  `make test-unit && make test-regression`. This runs the same tests as the
  CI job's `pytest -m "unit or regression"`, just as two processes instead of
  one.
- **`backend-unit` red on lint** — the job runs `ruff format --check` before
  `ruff check`, same order as `make format-check && make lint-backend`. A
  contributor whose code is check-clean but not format-clean is caught here.
- **`backend-unit` red on coverage** — reads
  `FAIL Required test coverage of 80% not reached. Total coverage: NN.NN%`.
- **`backend-integration` red with `MinIO never became ready`** — the service
  did not come up in time; the job prints `docker logs minio` on failure.
- **`backend-integration` red on a test** — reproduce with
  `make services-test-up && make test-integration`.
- **`frontend` red in the build step** — the tests are type-checked too
  (`tsconfig.app.json` includes `tests/**/*.ts` alongside `src/**/*`);
  `npm run build` (`vue-tsc -b && vite build`) reproduces it.

## Coverage

The backend gate is `COVERAGE_MIN`, 80, enforced by `make coverage-backend`
(three suite processes `--cov-append`-ed into one report, with `--fail-under`
applied once to the combined total — currently 96%) and separately by the
`backend-unit` CI job (unit + regression only, since that job has no services).

**The `backend-unit` gate has an exclusion, and it is deliberate.** That job
passes `--cov-config=backend/.coveragerc.ci`, which omits
`app/repositories/files.py` and `app/scripts/backfill.py`. Both are exercised
only against a real PostgreSQL, so a service-less `-m "unit or regression"`
run cannot reach them: `FakeFileRepository` *substitutes* for the repository
rather than exercising it, so no amount of unit testing against the fake would
add a single covered line to the real module. Un-omitted they drag that job to
76% and it fails the 80 gate for a reason unrelated to test quality.

They are gated instead by `backend-integration`: 14 tests in
`tests/integration/test_repository.py` and 8 in
`tests/integration/test_backfill.py`. Those 22 alone reach 99% of the
repository and 83% of the backfill script — the single repository miss is the
`file_repository` DI factory, which they bypass by constructing
`FileRepository` directly. The factory is covered by the rest of the suite
(`test_files_minio.py` drives the real routes), so `-m integration` as a whole,
and `make coverage-backend`, both report 100% for the repository.

The threshold itself was not lowered and no test was weakened. With the
omission the job measures 284 statements and reports 96%; `make
coverage-backend` still measures all 415, omits nothing, and also reports 96%.

The frontend reports coverage (`make coverage-frontend`, around
74% at the time of writing) but has no threshold yet: set one in
`frontend/vite.config.ts`'s `test.coverage` block from the first measured CI
baseline, and never below it.
