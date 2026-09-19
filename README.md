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
make backend    # http://localhost:8000 (docs at /docs)
make frontend   # http://localhost:5173
```

The Vite dev server proxies `/api` to the backend, so no CORS setup is needed in
development. `VITE_API_BASE_URL` overrides the API base URL when the frontend is
served separately from the backend.

## Checks

```sh
make test    # pytest
make lint    # ruff
make build   # vue-tsc + vite build
```

## Configuration

Backend settings are read from the environment with the `DARKANGEL_` prefix (see
`backend/.env.example`); frontend settings use Vite's `VITE_` prefix (see
`frontend/.env.example`).

## Deployment

GitHub Actions builds both services as container images and tells a local
Portainer instance to redeploy them.

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `.github/workflows/ci.yml` | every push and PR | ruff + pytest, `vue-tsc` + `vite build` |
| `.github/workflows/deploy.yml` | push to `main`, or manual | builds and pushes both images to GHCR, then calls the Portainer stack webhook |

Images are published as `ghcr.io/<owner>/darkangel-backend` and
`ghcr.io/<owner>/darkangel-frontend`, tagged `latest` and `sha-<commit>`.

The frontend image serves the built SPA with nginx and proxies `/api` to the
`backend` service (`frontend/nginx.conf`), so the API is same-origin and needs no
CORS configuration.

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

### Running the stack locally

```sh
cd deploy && IMAGE_OWNER=<owner> docker compose -f portainer-stack.yml up -d
```

The SPA is then on http://localhost:8080 and the API on http://localhost:8080/api.
