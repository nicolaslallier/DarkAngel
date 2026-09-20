# Testing

Three backend suites and two frontend ones. Which suite a test belongs to is
decided by the directory it sits in — there are no marker decorators to
remember and none to forget.

## The layers

| Suite | Where | What is real | CI job |
|---|---|---|---|
| Backend unit | `backend/tests/unit/` (5 tests) | Nothing outside the process. MinIO is `FakeMinio`, Keycloak is a fake JWKS. | `backend-unit` |
| Backend regression | `backend/tests/regression/` (14 tests) | Same as unit. Each file pins one fixed bug, or the API contract. | `backend-unit` |
| Backend integration | `backend/tests/integration/` (9 tests) | A real MinIO, in a throwaway bucket. Auth stays faked. | `backend-integration` |
| Frontend unit | `frontend/tests/unit/` | jsdom. `fetch` and `oidc-client-ts` are mocked. | `frontend` |
| Frontend regression | `frontend/tests/regression/` | Same as frontend unit. | `frontend` |

Frontend unit + regression together are 36 tests across 8 files (Vitest 5,
`frontend/vite.config.ts`'s `test` block).

Keycloak is never real, not even in integration: the suite signs its own RS256
tokens against a key it generates, and `backend/tests/conftest.py`'s
`fake_jwks` fixture (autouse, everywhere) swaps the JWKS client out for one
that returns that key. Only storage is real, and only in the integration
suite.

## Running them

```sh
make test              # every suite; integration skips if MinIO is down
make test-unit         # backend unit only, no services needed
make test-regression   # backend regression only
make test-integration  # backend integration; needs MinIO
make test-frontend     # vitest
make coverage          # both sides; the backend fails under 80%
```

Integration needs a MinIO on `localhost:9000`:

```sh
make minio-test-up     # docker-compose.test.yml, MinIO alone
make test-integration
make minio-test-down   # also drops its data
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

## The integration bucket

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

**Unreachable MinIO skips locally and fails in CI.** The switch is the `CI`
environment variable, which GitHub Actions sets to `true` for the
`backend-integration` job. A broken service must never turn a CI run green by
skipping.

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
container, then the integration suite), and `frontend` (vitest with coverage,
then `npm run build`, which type-checks with `vue-tsc` first). Each uploads its
JUnit XML as a workflow artifact — `backend-unit` also uploads `coverage.xml`
(the only job with a `--cov` flag; `backend-integration` has none), and
`frontend` also uploads its `lcov.info`; that's what the workflow file is
configured to do; as of this writing nothing has been pushed to trigger it on
GitHub yet, so treat this section as intent rather than an observed run.

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
  `make minio-test-up && make test-integration`.
- **`frontend` red in the build step** — the tests are type-checked too
  (`tsconfig.app.json` includes `tests/**/*.ts` alongside `src/**/*`);
  `npm run build` (`vue-tsc -b && vite build`) reproduces it.

## Coverage

The backend gate is `COVERAGE_MIN`, 80, enforced by `make coverage-backend`
(three suite processes `--cov-append`-ed into one report, with `--fail-under`
applied once to the combined total — currently around 97%) and separately by
the `backend-unit` CI job (unit + regression only, since that job never has a
real MinIO). The frontend reports coverage (`make coverage-frontend`, around
74% at the time of writing) but has no threshold yet: set one in
`frontend/vite.config.ts`'s `test.coverage` block from the first measured CI
baseline, and never below it.
