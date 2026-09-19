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

## Deployment

`.github/workflows/deploy.yml` runs on a push to `main` (or on demand). It builds
both services as container images, pushes them to GHCR, and then calls a webhook
that tells a local Portainer instance to redeploy the stack.

Images are published as `ghcr.io/<owner>/darkangel-backend` and
`ghcr.io/<owner>/darkangel-frontend`, tagged `latest` and `sha-<commit>`.

The frontend image serves the built SPA with nginx and proxies `/api` to the
`backend` service (`frontend/nginx.conf`), so the API is same-origin in
production and needs no CORS configuration.

### One-time setup

1. **Create the stack in Portainer** from `deploy/portainer-stack.yml`
   (Stacks → Add stack → Web editor, or the Repository method pointed at this
   repo). Set `IMAGE_OWNER` to your lowercase GHCR namespace; `FRONTEND_PORT`
   defaults to `8080`.
2. **If the GHCR packages are private**, add a registry with your GitHub
   username and a PAT that has `read:packages` under Portainer → Registries, so
   the stack can pull.
3. **Create the stack webhook** (Portainer → the stack → Webhooks) and save the
   URL as the repository secret `PORTAINER_WEBHOOK_URL`.

### Repository variables

Both are optional:

- `DEPLOY_RUNNER` — a self-hosted runner label. Set this when Portainer is only
  reachable from inside your network; GitHub-hosted runners cannot reach it.
  Defaults to `ubuntu-latest`.
- `PORTAINER_INSECURE` — set to `true` when Portainer serves a self-signed
  certificate, which adds `--insecure` to the webhook call.

### Testing the images locally

```sh
make docker-build   # build both images
make stack-up       # run the stack; SPA on http://localhost:8080
make stack-logs     # follow both services
make stack-down     # stop it
```

`make stack-up` serves the API at http://localhost:8080/api through the same
nginx proxy the deployed stack uses.

## Configuration

Backend settings are read from the environment with the `DARKANGEL_` prefix (see
`backend/.env.example`); frontend settings use Vite's `VITE_` prefix (see
`frontend/.env.example`).
