SHELL := /bin/bash

PY := uv run
PKG := src/darkangel
TESTS := tests

.PHONY: help install run fmt lint typecheck test test-unit test-integration test-regression build clean check

help:
	@echo "DarkAngel 1.1 - available targets:"
	@echo "  make install           Install dependencies (uv sync)"
	@echo "  make run              Run the API server"
	@echo "  make fmt              Format code with ruff"
	@echo "  make lint             Lint code with ruff"
	@echo "  make typecheck        Type-check with mypy"
	@echo "  make test             Run the full test suite"
	@echo "  make test-unit        Run unit tests"
	@echo "  make test-integration Run integration tests"
	@echo "  make test-regression  Run regression tests"
	@echo "  make build            Build distribution artifacts"
	@echo "  make clean            Remove caches and build artifacts"
	@echo "  make check            Run fmt, lint, typecheck and tests"

install:
	@$(PY) sync

run: install
	@$(PY) darkangel

fmt: install
	@$(PY) ruff format $(PKG) $(TESTS)

lint: install
	@$(PY) ruff check $(PKG) $(TESTS)

typecheck: install
	@$(PY) mypy $(PKG)

test: install
	@$(PY) pytest $(TESTS)

test-unit: install
	@$(PY) pytest $(TESTS)/test_api_unit.py

test-integration: install
	@$(PY) pytest $(TESTS)/test_api_integration.py

test-regression: install
	@$(PY) pytest $(TESTS)/test_api_regression.py

check: fmt lint typecheck test

build: install
	@$(PY) pyproject-build

clean:
	@rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache
	@find $(PKG) $(TESTS) -name '__pycache__' -type d -exec rm -rf {} +
