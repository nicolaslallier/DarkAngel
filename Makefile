SHELL := /bin/sh

.PHONY: help install test backend gui lock add add-dev build clean python-version

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Sync the environment (uv sync)
	uv sync

test: ## Run the full test suite
	uv run pytest

backend: ## Run the FastAPI backend
	uv run darkangel

gui: ## Run the PySide6 GUI (backend must already be running)
	uv run darkangel-gui

add: ## Add a runtime dependency: make add PKG=pkg-name
	uv add $(PKG)

add-dev: ## Add a dev dependency: make add-dev PKG=pkg-name
	uv add --dev $(PKG)

lock: ## Regenerate uv.lock
	uv lock

build: ## Build wheel + sdist into dist/
	uv build

clean: ## Remove build artifacts and caches
	rm -rf dist/ build/
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis

python-version: ## Print the pinned Python version
	@cat .python-version
