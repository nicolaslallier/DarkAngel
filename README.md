# DarkAngel

FastAPI backend + Vue 3 frontend.

## Layout

```
backend/    FastAPI application (Python 3.11, uv)
frontend/   Vue 3 + Vite + TypeScript SPA
```

## Setup

```sh
make install
```

Or manually:

```sh
cd backend && uv venv --python 3.11 && uv pip install -e ".[dev]"
cd frontend && npm install
```

## Run

Two terminals:

```sh
make dev-backend    # http://localhost:8000 (docs at /docs)
make dev-frontend   # http://localhost:5173
```

The Vite dev server proxies `/api` to the backend, so no CORS setup is needed in
development. `VITE_API_BASE_URL` overrides the API base URL when the frontend is
served separately from the backend.

## Checks

```sh
make test          # pytest
make coverage      # pytest with coverage, fails under 80%
make lint          # ruff check + vue-tsc
make format        # apply ruff formatting
make format-check  # fail if the backend is unformatted
make build         # backend wheel + vue-tsc/vite build
```

## CI/CD

`make` is the single entrypoint; `.github/workflows/ci.yml` only calls these
targets, so the pipeline runs identically on a laptop and on a runner.

```sh
make ci        # what CI runs: install-ci, then the full gate
make verify    # the same gate against an existing install (faster, local)
make release   # build and collect artifacts into dist/
```

`make help` lists every target. `make doctor` checks the required tools are on
PATH. Useful overrides: `PORT`, `COVERAGE_MIN`, `PYTHON_VERSION`, `DIST`, and
`ARGS` for `make test-backend ARGS="-k health"`.

## Configuration

Backend settings are read from the environment with the `DARKANGEL_` prefix (see
`backend/.env.example`); frontend settings use Vite's `VITE_` prefix (see
`frontend/.env.example`).
