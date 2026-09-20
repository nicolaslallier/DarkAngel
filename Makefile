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
	@command -v $(NPM) >/dev/null || { echo "missing: npm (Node.js 20+)"; exit 1; }
	@command -v $(NODE) >/dev/null || { echo "missing: node (Node.js 20+ — install via npm's runtime or https://nodejs.org)"; exit 1; }
	@echo "npm $$($(NPM) --version)"
	@echo "node $$($(NODE) --version)"

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
