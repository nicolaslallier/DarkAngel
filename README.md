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

## Authentication

Users sign in through the Infra Keycloak, realm `ea`
(`https://keycloak.famillelallier.net/realms/ea`), the same realm EA uses.

- **SPA** (`frontend/src/auth.ts`, `oidc-client-ts`): public client
  `darkangel-spa`, authorization code + PKCE. Every route but `/auth/callback`
  redirects to Keycloak when there is no token; tokens stay in memory, and
  `api/client.ts` sends the access token as `Authorization: Bearer`.
- **API** (`backend/app/core/auth.py`, PyJWT): a route takes the `Claims`
  dependency to require a valid token — RS256, issuer as above, audience
  `darkangel-api`. `GET /api/me` returns the caller; `/api/health` stays public.

**One-time setup:** create the client with `make keycloak-client`. It logs in to
the Keycloak admin API with `KEYCLOAK_ADMIN`/`KEYCLOAK_ADMIN_PASSWORD` (taken from
the environment, or from `INFRA_ENV`, default `../Infra/.env`) and is safe to
re-run. Users are the realm's existing users; create new ones in the admin
console.

Keycloak's certificate comes from the private Infra CA, which curl does not
trust on its own — `curl: (60) unable to get local issuer certificate`. The
script reads the CA from the Infra checkout next to `INFRA_ENV`
(`../Infra/certs/infra-ca.crt`), so with the two repos side by side it just
works. Otherwise:

```sh
KC_CACERT=/path/to/Infra/certs/infra-ca.crt make keycloak-client
KC_INSECURE=true make keycloak-client   # skip verification instead
```

**Local dev:** `make dev-frontend` logs in against the real realm (the client
allows `http://localhost:5173`). The backend fetches the realm's signing keys
over HTTPS, and Python does not trust the Infra CA the way the macOS keychain
does, so start it with:

```sh
SSL_CERT_FILE=/path/to/Infra/certs/infra-ca.crt make dev-backend
```

In the stack, `DARKANGEL_AUTH_JWKS_URL` reads the keys from
`http://keycloak:8080` over `infra-net` instead, so no CA is mounted.

## Files

The **Files** page (`/files`) keeps your home files in the Infra MinIO. Each
signed-in user sees only their own: the API (`/api/files`) stores them under
their Keycloak `sub` in bucket `darkangel-files`, as the MinIO user
`darkangel-api`, over `http://minio:9000` on `infra-net`. The browser never
talks to MinIO directly. Uploads are capped at 100 MB by the Infra NGINX
(`client_max_body_size` in `deploy/nginx/darkangel.conf`). The bucket is
versioned, so a file that was overwritten or deleted can be brought back from
the MinIO console.

**One-time setup:** put a secret of 8+ characters in `.portainer.env` as
`MINIO_SECRET_KEY`. Then, on the Docker host, run `make minio` to create the
bucket, the user, and its bucket-only policy, and `make up` to hand the secret
to the API. `make minio` reads the MinIO root credentials from the Infra `.env`
(`INFRA_ENV`, default `../Infra/.env`). It runs `mc` in a throwaway container on
`infra-net`, and it is safe to re-run: to rotate the secret, change it and run
both targets again.

## Deployment

GitHub Actions builds both services as container images and tells a local
Portainer instance to redeploy them.

| Workflow | Trigger | What it does |
| --- | --- | --- |
| `.github/workflows/ci.yml` | every push and PR | ruff + pytest, `vue-tsc` + `vite build` |
| `.github/workflows/deploy.yml` | push to `main`, or manual | builds and pushes both images to GHCR, then has Portainer redeploy the stack |

Images are published as `ghcr.io/<owner>/darkangel-backend` and
`ghcr.io/<owner>/darkangel-frontend`, tagged `latest` and `sha-<commit>`.

The frontend image runs no web server. Its final stage is a one-shot publisher:
it copies the built SPA into the shared `darkangel-web` volume and exits, and
the Infra NGINX serves those files (`frontend/publish-assets.sh`).

### Ingress

DarkAngel adds no nginx of its own. The [Infra](https://github.com/nicolaslallier/Infra)
stack's NGINX is the only web server in front of it, so the deployed address is:

```
https://darkangel.infra.famillelallier.net
```

That NGINX serves the SPA's files directly out of the shared `darkangel-web`
volume, and proxies `/api/` to the `darkangel-api` container over `infra-net`
(no prefix rewrite: the FastAPI router is mounted at `/api` already). SPA and
API therefore answer on one origin, so no CORS configuration is needed. The
hostname is covered by the `*.infra.famillelallier.net` cert and DNS wildcard,
so no certificate SAN or DNS zone has to be added.

Two things live in the Infra repo. Copy
[`deploy/nginx/darkangel.conf`](deploy/nginx/darkangel.conf) to
`nginx/conf.d/darkangel.conf` there, add the `darkangel-web` volume to its
`nginx` service (the file's header has the exact lines), and run `make up` in
that repo — until that is done, DarkAngel is running but nothing routes to it.

### One-time setup

1. **Create the shared volume** on the Docker host — `docker volume create
   darkangel-web`. Both stacks declare it `external`, the way they both use
   `infra-net`: this stack fills it, the Infra NGINX reads it.
2. **Store a Portainer access token** (Portainer → My account → Access tokens)
   as the repository secret `PORTAINER_API_KEY`. That is all `deploy.yml` needs:
   it creates the stack on the first run and redeploys it on every run after, so
   no one has to run `make up` by hand before the first deploy.
3. **If the GHCR packages are private**, add a registry with your GitHub
   username and a PAT that has `read:packages` under Portainer → Registries, so
   the stack can pull.
4. **Tell the workflow how to reach Portainer** — see the variables below. From
   a GitHub-hosted runner it uses the public origin
   (`https://portainer.infra.famillelallier.net`); if Portainer is not exposed
   there, a hosted runner cannot deploy at all — stand up the self-hosted runner
   (`make runner-up`, see "Deploying from a self-hosted runner") and set
   `DEPLOY_RUNNER` to `darkangel`.
5. **Provision file storage** — set `MINIO_SECRET_KEY` in `.portainer.env` and
   run `make minio` (see [Files](#files)).

To drive the same stack from a laptop, copy `.portainer.env.example` to
`.portainer.env` and put the same token in it (it is gitignored: the token is
Docker-daemon-root, so it never goes in `.env`, which is handed to containers,
or in the repository). `make up` then does locally what the workflow does.

Nothing about this depends on the machine that deploys being able to pull:
Portainer, on the Docker host, does the pulling with its own registry
credentials. A broken local credential helper — Docker Desktop's
`error getting credentials … A specified logon session does not exist` under
WSL — cannot break the deploy.

### Repository secrets

- `PORTAINER_API_KEY` — a Portainer access token. The path above: the workflow
  creates or redeploys the stack through Portainer's API.
- `PORTAINER_WEBHOOK_URL` — optional fallback, used only when there is no API
  key. It redeploys a stack that already exists but cannot create one, so it
  still needs a first `make up`; `make webhook` prints the URL.
- `MINIO_SECRET_KEY` — the secret `make minio` gave the `darkangel-api` MinIO
  user, the same value as in `.portainer.env`. The API path rewrites the
  stack's whole environment on each deploy, so without it every run blanks the
  key and the Files page stops working. Not needed on the webhook path, which
  leaves the stack's environment alone.

### Repository variables

All optional:

- `DEPLOY_RUNNER` — a self-hosted runner label. Set it to `darkangel` when
  Portainer is only reachable from inside your network; GitHub-hosted runners
  cannot reach it. Defaults to `ubuntu-latest`. That runner is `make runner-up`
  below, and on the Docker host it reaches Portainer over `infra-net`, the way
  `make up` does from that network.
- `PORTAINER_URL` — Portainer's API origin, when it is neither
  `https://portainer.infra.famillelallier.net` (GitHub-hosted runner) nor
  `https://portainer:9443` (self-hosted runner on `infra-net`).
- `PORTAINER_DIRECT` — `true` to reach Portainer straight from the runner,
  `false` to go through a container on `infra-net`. Defaults to `false` when
  `DEPLOY_RUNNER` is set and `true` otherwise, which is usually right.
- `PORTAINER_INSECURE` — set to `true` when Portainer serves a self-signed
  certificate, which skips certificate verification.

### Running the stack

```sh
make up        # create/redeploy in Portainer, re-pulling the GHCR images
make pull      # redeploy an existing stack, re-pulling
make down      # stop the stack (images and volumes kept)
make delete    # remove the stack from Portainer
make webhook   # print the redeploy webhook URL
make ps logs   # status / follow logs on this docker host (SERVICE=darkangel-api)
make deploy    # redeploy via PORTAINER_WEBHOOK_URL (deploy.yml's fallback path)
```

`IMAGE_OWNER` and `IMAGE_TAG` are passed through to the stack: `make up
IMAGE_TAG=sha-<commit>` rolls to a specific build. `PORTAINER_URL`,
`PORTAINER_NETWORK` and `PORTAINER_REF` cover a setup that differs from the
Infra stack's defaults (Portainer on `infra-net`, deploying `main`).

On a host with no Portainer, `make up-local` / `make down-local` run the same
stack file through the local docker daemon. That path pulls from GHCR itself,
so it needs a working `docker login ghcr.io` on that machine, and it creates
`infra-net` and `darkangel-web` if the Infra stack has not.

Either way the containers publish nothing: the stack is reached through the
Infra NGINX at `https://darkangel.infra.famillelallier.net` (API under `/api`).
`web-assets` is expected to sit in `Exited (0)` — it publishes the build and
stops; only `darkangel-api` keeps running. On a host without that NGINX there is
nothing to serve the SPA, so look at it with `make dev-backend` and
`make dev-frontend` instead.

### Deploying from a self-hosted runner

Only needed when Portainer has **no public ingress**. By default `deploy.yml`
runs on a GitHub-hosted runner and reaches Portainer at its public origin; where
that origin does not exist, a hosted runner cannot deploy at all and the job has
to run on the LAN instead — the same constraint the
[Infra](https://github.com/nicolaslallier/Infra) stack deploys under, and the
same runner setup, mirrored here.

A runner registered on this repository serves only this repository, so Infra's
runner cannot take DarkAngel's jobs: this stack needs its own.

Like Infra's, it is its own compose project (`docker-compose.runner.yml`),
outside the stack it deploys — a redeploy force-recreates every container in
`darkangel`, and a runner recreated mid-job is a job that never reports.

```bash
# 1. a PAT that may register runners on this repo:
#    GitHub -> Settings -> Developer settings -> Personal access tokens
#    (classic, 'repo' scope; or fine-grained with Administration: RW here)
#    Minting it is a web-UI step -- GitHub has no API that issues a PAT.
make runner-env         # paste it; writes .runner.env, mode 600

# 2. start it; it registers itself with the label 'darkangel'
make runner-up
make runner-status      # the container here, and what GitHub has registered
make runner-logs        # until "Listening for Jobs"
```

Then set the **`DEPLOY_RUNNER` repository variable to `darkangel`**, which is
what moves the deploy job onto it. Without that the runner sits idle and the
deploy keeps going out from a GitHub-hosted runner.

| Target | What it does |
| --- | --- |
| `make runner-up` / `make runner-down` | Start / stop the runner |
| `make runner-restart` / `make runner-logs` | Restart it / tail its logs |
| `make runner-status` | Is it running here, and does GitHub have it registered with the `darkangel` label? |
| `make runner-pull` | Re-pull the runner image and recreate it (the tag moves; GitHub retires old versions) |
| `make runner-shell` | Open a shell in the running runner |
| `make runner-env` | Write `.runner.env` from a GitHub PAT, checking first that it may administer runners (`FORCE=1` replaces it) |

`make runner-status` is worth preferring over the browser, because it answers
the question that actually bites: the runner is `EPHEMERAL`, so it de-registers
after every job and re-registers on restart, and a container that is up says
nothing about whether GitHub still has a runner to hand the next deploy to.
GitHub does not report the gap — a job whose labels match nothing queues
silently rather than failing — so the two sides have to be read together.

`runner-down`, `-restart` and `-pull` refuse while a job is running, since
recreating the container mid-job leaves that workflow run with no result;
`FORCE=1 make runner-down` overrides. If GitHub cannot be reached to ask, they
warn and continue rather than trapping you with a runner you cannot stop.
`make runner-pull` is the fix for a runner GitHub has stopped accepting: the
image tag moves on purpose, because pinning a digest ages into a version the
service refuses.

Two things worth knowing before relying on it. The runner holds
`/var/run/docker.sock` — root on this daemon, the same power Portainer's UI
has — so **merging to main is now enough to run code on the host**; branch
protection is what keeps that set small. And **no workflow on the `darkangel`
label may ever trigger on `pull_request`**: this repository is public, and a
fork's PR brings its own workflow file. Set **Settings → Actions → General →
"Fork pull request workflows from outside collaborators"** to *Require approval
for all outside collaborators*.

`.runner.env` holds that PAT and is gitignored, for the reason
`.portainer.env` is: it can register runners on the repository, so it stays out
of anything handed to a container.
