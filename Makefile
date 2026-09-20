# DarkAngel — single entrypoint for developer and CI/CD tasks.
#
# Every target is safe to run from CI: they are non-interactive, fail fast, and
# call tools through the project-local toolchains (backend/.venv, node_modules).
#
#   make help        list every target
#   make ci          the full gate CI runs on a pull request
#   make release     build the distributable artifacts

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

BACKEND  := backend
FRONTEND := frontend
VENV     := $(BACKEND)/.venv
PY       := $(VENV)/bin/python
UV       ?= uv
NPM      ?= npm
NODE     ?= node
PYTHON_VERSION ?= 3.11

# Backend dev server port; override with `make dev-backend PORT=9000`.
PORT ?= 8000

# Minimum line coverage enforced by `make coverage`.
COVERAGE_MIN ?= 80

# Version reported by `make version`, read from the backend package metadata.
VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' $(BACKEND)/pyproject.toml | head -1)

# Dist directory collecting every artifact `make release` produces.
DIST ?= dist

.PHONY: help version doctor doctor-backend doctor-frontend \
        install install-backend install-frontend install-ci \
        dev-backend dev-frontend backend frontend \
        lint lint-backend lint-frontend format format-check typecheck \
        test test-backend coverage \
        build build-backend build-frontend preview \
        up pull down delete webhook stack-selftest deploy keycloak-client minio \
        runner-env check-runner-env runner-up runner-down runner-restart \
        runner-logs runner-status runner-pull runner-shell \
        up-local down-local restart ps logs \
        ci verify release clean clean-backend clean-frontend distclean

## === meta ===

help: ## List the available targets
	@echo "DarkAngel $(VERSION)"
	@echo "Usage: make [TARGET] [VAR=value]"
	@awk '/^## === / { sub(/^## === /,""); sub(/ ===/,""); printf "\n    \033[1m%s\033[0m\n", $$0; next } /^[a-zA-Z0-9_-]+:.* ## / { n=split($$0,p," ## "); m=split(p[1],q,":"); printf "    \033[36m%-16s\033[0m %s\n", q[1], p[2] }' $(MAKEFILE_LIST)

version: ## Print the project version
	@echo "$(VERSION)"

doctor: doctor-backend doctor-frontend ## Check that all required tools are on PATH
doctor-backend: ## Check the backend toolchain (uv) is on PATH
	@command -v $(UV) >/dev/null || { echo "missing: uv (https://docs.astral.sh/uv/)"; exit 1; }
	@echo "uv    $$($(UV) --version)"
doctor-frontend: ## Check the frontend toolchain (npm + node) is on PATH
	@{ \
		command -v $(NPM) >/dev/null || { echo "missing: npm (Node.js 20+)"; exit 1; }; \
		npm_dir=$$(dirname "$$(command -v $(NPM) 2>/dev/null || true)" 2>/dev/null || true); \
		node_bin=$$(command -v $(NODE) 2>/dev/null || :); \
		if [ -z "$$node_bin" ]; then \
			for d in \
				"$$npm_dir" \
				"$$HOME/.nvm/versions/node"/*/bin \
				"$$HOME/.fnm/node-versions"/*/installation/bin \
				"$$HOME/.asdf/installs/nodejs"/*/bin \
				"$$HOME/.volta/bin" \
				"/usr/local/bin" "/opt/homebrew/bin" "/usr/local/node/bin"; do \
				for c in "$$d/node"; do \
					if [ -x "$$c" ]; then node_bin="$$c"; break 2; fi; \
				done; \
			done; \
		fi; \
		if [ -z "$$node_bin" ]; then \
			echo "missing: node (npm found, but node is not on PATH — re-source your version manager, or install Node 20+ via https://nodejs.org)"; \
			exit 1; \
		fi; \
		echo "npm $$($(NPM) --version)"; \
		echo "node $$($$node_bin --version)"; \
	}

## === install ===

install: install-backend install-frontend ## Install both sides for local development

install-backend: doctor-backend ## Create backend/.venv and install the backend with dev extras
	cd $(BACKEND) && $(UV) venv --python $(PYTHON_VERSION) && $(UV) pip install -e ".[dev]"

install-frontend: doctor-frontend ## Install frontend dependencies (npm install)
	cd $(FRONTEND) && $(NPM) install

install-ci: doctor ## Reproducible install for CI (locked frontend deps)
	cd $(BACKEND) && $(UV) venv --python $(PYTHON_VERSION) && $(UV) pip install -e ".[dev]"
	cd $(FRONTEND) && $(NPM) ci

# Guard: every target below needs the backend venv to exist.
$(PY):
	@echo "backend/.venv is missing — run 'make install-backend' first." >&2
	@exit 1

## === dev loop ===

dev-backend: $(PY) ## Run the backend with autoreload on PORT (default 8000)
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --port $(PORT)

dev-frontend: ## Run the Vite dev server on :5173
	cd $(FRONTEND) && $(NPM) run dev

# Backwards-compatible aliases for the previous target names.
backend: dev-backend
frontend: dev-frontend

preview: build-frontend ## Serve the production frontend build locally
	cd $(FRONTEND) && $(NPM) run preview

## === quality ===

lint: lint-backend lint-frontend ## Lint both sides

lint-backend: $(PY) ## ruff check on the backend
	cd $(BACKEND) && .venv/bin/python -m ruff check .

lint-frontend: typecheck ## Type-check the frontend (no separate JS linter yet)

typecheck: ## vue-tsc type-check without emitting
	cd $(FRONTEND) && $(NPM) exec -- vue-tsc --build --force

format: $(PY) ## Apply ruff formatting and import fixes to the backend
	cd $(BACKEND) && .venv/bin/python -m ruff format .
	cd $(BACKEND) && .venv/bin/python -m ruff check --fix .

format-check: $(PY) ## Fail if the backend is not formatted (CI gate)
	cd $(BACKEND) && .venv/bin/python -m ruff format --check .

test: test-backend ## Run the test suites

test-backend: $(PY) ## pytest; pass extra args with ARGS="tests/test_health.py -k ok"
	cd $(BACKEND) && .venv/bin/python -m pytest $(ARGS)

coverage: $(PY) ## pytest with coverage, failing under COVERAGE_MIN%
	cd $(BACKEND) && .venv/bin/python -m pytest \
		--cov=app --cov-report=term-missing --cov-report=xml \
		--cov-fail-under=$(COVERAGE_MIN)

## === build ===

build: build-backend build-frontend ## Build both distributables

build-backend: doctor-backend ## Build the backend wheel and sdist into backend/dist
	cd $(BACKEND) && $(UV) build

build-frontend: doctor-frontend ## Type-check and build the SPA into frontend/dist
	cd $(FRONTEND) && $(NPM) run build

## === pipelines ===

verify: format-check lint test build ## Every check, against an existing install
	@echo "verify: ok"

ci: install-ci verify ## Full CI gate: reproducible install then every check
	@echo "ci: ok ($(VERSION))"

release: clean build ## Collect the release artifacts under dist/
	@mkdir -p $(DIST)
	@cp $(BACKEND)/dist/* $(DIST)/
	@tar -czf $(DIST)/darkangel-frontend-$(VERSION).tar.gz -C $(FRONTEND)/dist .
	@echo "release: artifacts in $(DIST)/"
	@ls -1 $(DIST)

## === stack ===
# Portainer owns the stack, exactly as it owns the "infra" one: it deploys
# deploy/portainer-stack.yml from GitHub and pulls the GHCR images on the
# Docker host with the credentials held in Portainer -> Registries. That keeps
# the pull off this machine, where Docker Desktop's credential helper can fail
# with "A specified logon session does not exist".
#
# The stack runs no web server and publishes no host port: the Infra stack's
# NGINX serves the SPA and the API at https://darkangel.infra.famillelallier.net
# (deploy/nginx/darkangel.conf). The `darkangel-web` volume it reads is shared,
# so it has to exist on the host: `docker volume create darkangel-web`.
#
# Overrides: IMAGE_OWNER, IMAGE_TAG (the stack's variables), and
# PORTAINER_URL / PORTAINER_NETWORK / PORTAINER_REF for an unusual setup.

STACK_SH := ./scripts/portainer-stack.sh

up: ## Deploy/redeploy the stack in Portainer, re-pulling the GHCR images
	$(STACK_SH) up

pull: ## Redeploy an existing Portainer stack, re-pulling the images
	$(STACK_SH) pull

down: ## Stop the stack in Portainer (images and volumes kept)
	$(STACK_SH) down

delete: ## Remove the stack from Portainer entirely
	$(STACK_SH) delete

webhook: ## Print the stack's redeploy webhook (creating one if needed)
	@$(STACK_SH) webhook

stack-selftest: ## Check portainer-stack.sh's helpers without calling Portainer
	@$(STACK_SH) selftest

keycloak-client: ## Create/update the darkangel-spa client in Keycloak realm ea (INFRA_ENV, KC_CACERT)
	@scripts/provision-keycloak-client.sh

minio: ## Create/update the Infra MinIO bucket + user home files live in (INFRA_ENV, .portainer.env)
	@scripts/provision-minio.sh

# The webhook only redeploys; stopping the stack is `make down`, and creating
# one is `make up`. deploy.yml prefers the API path (PORTAINER_API_KEY), which
# can do both, and falls back to this webhook when no token is set.
deploy: ## Redeploy the Portainer stack via PORTAINER_WEBHOOK_URL (CI's fallback)
	@test -n "$$PORTAINER_WEBHOOK_URL" || { echo "set PORTAINER_WEBHOOK_URL (get it from 'make webhook')" >&2; exit 1; }
	curl --silent --show-error --fail-with-body --location --max-time 120 \
		$${PORTAINER_INSECURE:+--insecure} -X POST "$$PORTAINER_WEBHOOK_URL"
	@echo "deploy: Portainer accepted the redeploy request"

## === CI runner ===
# A self-hosted GitHub Actions runner, so deploy.yml can reach a Portainer
# that has no public ingress. It lives in its own compose project, outside
# the stack it deploys: a redeploy force-recreates every container in
# "darkangel", and a runner recreated mid-job never reports.
#
# Only needed when the hosted-runner path cannot work. deploy.yml reaches
# Portainer's public origin from a GitHub-hosted runner by default; set the
# DEPLOY_RUNNER repository variable to 'darkangel' to move that job here.
#
# --env-file .runner.env is not a convenience: without it compose would
# interpolate from this repo's .env. The runner needs one value and has no
# business seeing the rest.
RUNNER_COMPOSE := docker compose -f docker-compose.runner.yml --env-file .runner.env

# The PAT cannot be generated by anything here -- GitHub mints those in its
# web UI only. This takes it from there: it checks the token can actually
# administer runners on this repo before writing it, since a token with the
# wrong scope registers nothing and only says so in the runner's own logs.
runner-env: ## Write .runner.env from a GitHub PAT (FORCE=1 replaces it)
	@./scripts/gen-runner-env.sh $(if $(filter 1,$(FORCE)),--force,)

check-runner-env:
	@test -f .runner.env || { \
		echo "make: .runner.env not found -- run 'make runner-env' and paste a PAT that may register runners on this repo" >&2; \
		exit 1; \
	}

# Stopping or recreating the runner mid-job kills that job, and GitHub gets
# no result for it -- the workflow run simply stops reporting. Every target
# that does so asks scripts/runner-status.sh first; FORCE=1 means it.
RUNNER_NOT_BUSY = test -n "$(FORCE)" || ./scripts/runner-status.sh --busy

runner-up: check-runner-env ## Start the self-hosted CI runner (its own compose project)
	$(RUNNER_COMPOSE) up -d
	@echo
	@echo "Runner -> https://github.com/nicolaslallier/DarkAngel/settings/actions/runners"
	@echo "It must show up there with the label 'darkangel', and the DEPLOY_RUNNER"
	@echo "repository variable must be set to 'darkangel', before a push to main deploys here."
	@echo "Confirm with 'make runner-status'; 'make runner-logs' until \"Listening for Jobs\"."

runner-down: check-runner-env ## Stop the CI runner (FORCE=1 even mid-job)
	@$(RUNNER_NOT_BUSY)
	$(RUNNER_COMPOSE) down

runner-restart: check-runner-env ## Restart the CI runner (FORCE=1 even mid-job)
	@$(RUNNER_NOT_BUSY)
	$(RUNNER_COMPOSE) restart

runner-logs: check-runner-env ## Tail the CI runner's logs
	$(RUNNER_COMPOSE) logs -f

runner-status: check-runner-env ## Runner state: the container here, and what GitHub has registered
	@./scripts/runner-status.sh

# The image tag moves on purpose (GitHub retires old runner versions
# server-side, and then refuses to talk to them), so this is the fix for a
# runner the service has stopped accepting -- not routine housekeeping.
runner-pull: check-runner-env ## Re-pull the runner image and recreate it (FORCE=1 even mid-job)
	@$(RUNNER_NOT_BUSY)
	$(RUNNER_COMPOSE) pull
	$(RUNNER_COMPOSE) up -d

runner-shell: check-runner-env ## Open a shell in the running CI runner
	$(RUNNER_COMPOSE) exec runner bash

## === stack (local) ===
# The same stack file run by the local docker daemon, for a host with no
# Portainer. `up-local` pulls from GHCR itself, so it needs a working
# `docker login ghcr.io` on this machine. The stack file expects the shared
# `infra-net` network and `darkangel-web` volume to exist (the Infra stack is
# the other end of both), so up-local creates either when it does not -- on a
# host with no Infra NGINX nothing then serves the files the volume collects.

STACK := deploy/portainer-stack.yml
COMPOSE ?= docker compose -f $(STACK) -p darkangel

up-local: ## Start the stack with the local docker daemon (no Portainer)
	@docker network inspect infra-net >/dev/null 2>&1 || docker network create infra-net
	@docker volume inspect darkangel-web >/dev/null 2>&1 || docker volume create darkangel-web
	$(COMPOSE) up -d --pull always

down-local: ## Stop the locally-run stack
	$(COMPOSE) down

restart: ## Restart the stack's containers on this docker host
	$(COMPOSE) restart

ps: ## Show the stack's containers on this docker host
	$(COMPOSE) ps

logs: ## Follow the stack logs; limit with SERVICE=darkangel-api
	$(COMPOSE) logs -f $(SERVICE)

## === housekeeping ===

clean: clean-backend clean-frontend ## Remove build output and caches

clean-backend:
	rm -rf $(BACKEND)/dist $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache \
		$(BACKEND)/.coverage $(BACKEND)/coverage.xml $(BACKEND)/*.egg-info
	find $(BACKEND) -name __pycache__ -type d -prune -exec rm -rf {} +

clean-frontend:
	rm -rf $(FRONTEND)/dist $(FRONTEND)/node_modules/.vite

distclean: clean ## Also remove the installed toolchains and dist/
	rm -rf $(VENV) $(FRONTEND)/node_modules $(DIST)
