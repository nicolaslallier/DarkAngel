# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Overview

DarkAngel is a two-part application: a FastAPI backend (`backend/`) and a Vue 3
single-page frontend (`frontend/`). The frontend talks to the backend over HTTP
under the `/api` prefix.

## Commands

The root `Makefile` is the entrypoint for everything, including CI — run
`make help` for the full list. `make ci` is the exact gate
`.github/workflows/ci.yml` runs; `make verify` is that gate without the
reinstall. The raw equivalents:

```sh
make install                                   # install both sides
cd backend && .venv/bin/python -m pytest       # run backend tests
cd backend && .venv/bin/python -m pytest tests/test_health.py::test_health_returns_ok
cd backend && .venv/bin/python -m ruff check . # lint backend
cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev                     # Vite dev server on :5173
cd frontend && npm run build                   # type-check (vue-tsc) + build
```

The backend virtualenv is `backend/.venv`, created by `uv`; there is no global
Python environment for this project, so always call tools through `.venv/bin/`.

## Architecture

**Backend** (`backend/app/`)

- `main.py` — `create_app()` builds the `FastAPI` instance (CORS + router wiring);
  module-level `app` is what uvicorn serves.
- `api/router.py` — the single `api_router`, mounted at `/api`. New route modules
  go in `api/routes/` and are included here.
- `core/config.py` — `Settings` (pydantic-settings), read from the environment
  with the `DARKANGEL_` prefix or `backend/.env`. Access it through the cached
  `get_settings()`, never by instantiating `Settings()` directly.

**Frontend** (`frontend/src/`)

- `api/client.ts` — the only place `fetch` is called; per-resource modules
  (`api/health.ts`) wrap it and own the response types.
- `stores/` — Pinia setup stores holding async state (`loading`/`error`/data).
- `views/` + `router/index.ts` — routed pages; `@/` is aliased to `src/`.

Requests use the relative `/api` base so the Vite proxy (`vite.config.ts`)
forwards them to the backend in development. Setting `VITE_API_BASE_URL` points
the SPA at an absolute API URL instead; the backend's `cors_origins` setting must
then allow that frontend origin.

## Conventions

- Backend: ruff with a 100-column line length; response shapes are pydantic
  models declared next to their route.
- Frontend: `<script setup lang="ts">` single-file components; import from `@/`
  rather than with relative paths that climb directories.
