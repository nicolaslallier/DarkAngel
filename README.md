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

### Ingress

The stack publishes no host port. The [Infra](https://github.com/nicolaslallier/Infra)
stack's NGINX is the only ingress on that host, so the deployed address is:

```
https://darkangel.infra.famillelallier.net
```

The SPA container joins the shared `infra-net` network as `darkangel-web` and
that NGINX proxies the hostname to it; the backend stays on the stack's private
network, reachable only through the SPA container's own `/api` proxy. The
hostname is covered by the `*.infra.famillelallier.net` cert and DNS wildcard,
so no certificate SAN or DNS zone has to be added.

The vhost itself lives in the Infra repo. Copy
[`deploy/nginx/darkangel.conf`](deploy/nginx/darkangel.conf) to
`nginx/conf.d/darkangel.conf` there and run `make up` in that repo — until that
is done, DarkAngel is running but nothing routes to it.

### One-time setup

1. **Create `.portainer.env`** from `.portainer.env.example` and put a Portainer
   access token in it (Portainer → My account → Access tokens). It is
   gitignored: the token is Docker-daemon-root, so it never goes in `.env`
   (which is handed to containers) or in the repository.
2. **If the GHCR packages are private**, add a registry with your GitHub
   username and a PAT that has `read:packages` under Portainer → Registries, so
   the stack can pull.
3. **Run `make up`.** It creates the stack in Portainer from this repository
   (Repository method, `deploy/portainer-stack.yml` on `main`) and prints the
   redeploy webhook it minted. Save that URL as the repository secret
   `PORTAINER_WEBHOOK_URL` so `deploy.yml` can redeploy; `make webhook` prints
   it again later.

Nothing about this depends on the machine you run `make up` from being able to
pull: Portainer, on the Docker host, does the pulling with its own registry
credentials. A broken local credential helper — Docker Desktop's
`error getting credentials … A specified logon session does not exist` under
WSL — cannot break the deploy.

### Repository variables

Both are optional:

- `DEPLOY_RUNNER` — a self-hosted runner label. Set this when Portainer is only
  reachable from inside your network; GitHub-hosted runners cannot reach it.
  Defaults to `ubuntu-latest`.
- `PORTAINER_INSECURE` — set to `true` when Portainer serves a self-signed
  certificate, which adds `--insecure` to the webhook call.

### Running the stack

```sh
make up        # create/redeploy in Portainer, re-pulling the GHCR images
make pull      # redeploy an existing stack, re-pulling
make down      # stop the stack (images and volumes kept)
make delete    # remove the stack from Portainer
make webhook   # print the redeploy webhook URL
make ps logs   # status / follow logs on this docker host (SERVICE=backend)
make deploy    # redeploy via PORTAINER_WEBHOOK_URL, the way CI does
```

`IMAGE_OWNER` and `IMAGE_TAG` are passed through to the stack: `make up
IMAGE_TAG=sha-<commit>` rolls to a specific build. `PORTAINER_URL`,
`PORTAINER_NETWORK` and `PORTAINER_REF` cover a setup that differs from the
Infra stack's defaults (Portainer on `infra-net`, deploying `main`).

On a host with no Portainer, `make up-local` / `make down-local` run the same
stack file through the local docker daemon. That path pulls from GHCR itself,
so it needs a working `docker login ghcr.io` on that machine, and it creates
`infra-net` if the Infra stack has not.

Either way the containers publish nothing: the stack is reached through the
Infra NGINX at `https://darkangel.infra.famillelallier.net` (API under `/api`).
To look at the SPA on a host without that NGINX, publish it ad hoc —
`docker compose -f deploy/portainer-stack.yml -p darkangel run --rm -p 8080:80
darkangel-web`, then http://localhost:8080 — or just run `make dev-backend` and
`make dev-frontend`.
