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
PYTHON_VERSION ?= 3.11

# Backend dev server port; override with `make dev-backend PORT=9000`.
PORT ?= 8000

# Minimum line coverage enforced by `make coverage`.
COVERAGE_MIN ?= 80

# Version reported by `make version`, read from the backend package metadata.
VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' $(BACKEND)/pyproject.toml | head -1)

# Dist directory collecting every artifact `make release` produces.
DIST ?= dist

.PHONY: help version doctor \
        install install-backend install-frontend install-ci \
        dev-backend dev-frontend backend frontend \
        lint lint-backend lint-frontend format format-check typecheck \
        test test-backend coverage \
        build build-backend build-frontend preview \
        up pull down delete webhook stack-selftest deploy \
        up-local down-local restart ps logs \
        ci verify release clean clean-backend clean-frontend distclean

## ---------------------------------------------------------------- meta -----

help: ## List the available targets
	@echo "DarkAngel $(VERSION)"
	@echo
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

version: ## Print the project version
	@echo "$(VERSION)"

doctor: ## Check that the required tools are on PATH
	@command -v $(UV) >/dev/null || { echo "missing: uv (https://docs.astral.sh/uv/)"; exit 1; }
	@command -v $(NPM) >/dev/null || { echo "missing: npm (Node.js 20+)"; exit 1; }
	@echo "uv  $$($(UV) --version)"
	@echo "npm $$($(NPM) --version)"
	@echo "node $$(node --version)"

## ------------------------------------------------------------- install -----

install: install-backend install-frontend ## Install both sides for local development

install-backend: doctor ## Create backend/.venv and install the backend with dev extras
	cd $(BACKEND) && $(UV) venv --python $(PYTHON_VERSION) && $(UV) pip install -e ".[dev]"

install-frontend: doctor ## Install frontend dependencies (npm install)
	cd $(FRONTEND) && $(NPM) install

install-ci: doctor ## Reproducible install for CI (locked frontend deps)
	cd $(BACKEND) && $(UV) venv --python $(PYTHON_VERSION) && $(UV) pip install -e ".[dev]"
	cd $(FRONTEND) && $(NPM) ci

# Guard: every target below needs the backend venv to exist.
$(PY):
	@echo "backend/.venv is missing — run 'make install-backend' first." >&2
	@exit 1

## ------------------------------------------------------------ dev loop -----

dev-backend: $(PY) ## Run the backend with autoreload on PORT (default 8000)
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --port $(PORT)

dev-frontend: ## Run the Vite dev server on :5173
	cd $(FRONTEND) && $(NPM) run dev

# Backwards-compatible aliases for the previous target names.
backend: dev-backend
frontend: dev-frontend

preview: build-frontend ## Serve the production frontend build locally
	cd $(FRONTEND) && $(NPM) run preview

## ------------------------------------------------------------- quality -----

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

## --------------------------------------------------------------- build -----

build: build-backend build-frontend ## Build both distributables

build-backend: doctor ## Build the backend wheel and sdist into backend/dist
	cd $(BACKEND) && $(UV) build

build-frontend: ## Type-check and build the SPA into frontend/dist
	cd $(FRONTEND) && $(NPM) run build

## ----------------------------------------------------------- pipelines -----

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

## ------------------------------------------------------------- stack -----
# Portainer owns the stack, exactly as it owns the "infra" one: it deploys
# deploy/portainer-stack.yml from GitHub and pulls the GHCR images on the
# Docker host with the credentials held in Portainer -> Registries. That keeps
# the pull off this machine, where Docker Desktop's credential helper can fail
# with "A specified logon session does not exist".
#
# The stack publishes no host port: the Infra stack's NGINX fronts it at
# https://darkangel.infra.famillelallier.net (deploy/nginx/darkangel.conf).
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

# ponytail: the webhook only redeploys; stopping the stack is `make down`.
deploy: ## Redeploy the Portainer stack via PORTAINER_WEBHOOK_URL (what CI calls)
	@test -n "$$PORTAINER_WEBHOOK_URL" || { echo "set PORTAINER_WEBHOOK_URL (get it from 'make webhook')" >&2; exit 1; }
	curl --silent --show-error --fail-with-body --location --max-time 120 \
		$${PORTAINER_INSECURE:+--insecure} -X POST "$$PORTAINER_WEBHOOK_URL"
	@echo "deploy: Portainer accepted the redeploy request"

## --------------------------------------------------------- stack (local) ---
# The same stack file run by the local docker daemon, for a host with no
# Portainer. `up-local` pulls from GHCR itself, so it needs a working
# `docker login ghcr.io` on this machine. The stack file expects `infra-net` to
# exist (the Infra stack owns it), so up-local creates it when it does not --
# without an nginx on that network, reach the SPA from another container on it.

STACK := deploy/portainer-stack.yml
COMPOSE ?= docker compose -f $(STACK) -p darkangel

up-local: ## Start the stack with the local docker daemon (no Portainer)
	@docker network inspect infra-net >/dev/null 2>&1 || docker network create infra-net
	$(COMPOSE) up -d --pull always

down-local: ## Stop the locally-run stack
	$(COMPOSE) down

restart: ## Restart the stack's containers on this docker host
	$(COMPOSE) restart

ps: ## Show the stack's containers on this docker host
	$(COMPOSE) ps

logs: ## Follow the stack logs; limit with SERVICE=backend
	$(COMPOSE) logs -f $(SERVICE)

## ---------------------------------------------------------- housekeeping ---

clean: clean-backend clean-frontend ## Remove build output and caches

clean-backend:
	rm -rf $(BACKEND)/dist $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache \
		$(BACKEND)/.coverage $(BACKEND)/coverage.xml $(BACKEND)/*.egg-info
	find $(BACKEND) -name __pycache__ -type d -prune -exec rm -rf {} +

clean-frontend:
	rm -rf $(FRONTEND)/dist $(FRONTEND)/node_modules/.vite

distclean: clean ## Also remove the installed toolchains and dist/
	rm -rf $(VENV) $(FRONTEND)/node_modules $(DIST)
