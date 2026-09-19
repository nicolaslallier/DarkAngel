.PHONY: install backend frontend test lint build

install:
	cd backend && uv venv --python 3.11 && uv pip install -e ".[dev]"
	cd frontend && npm install

backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && .venv/bin/python -m pytest

lint:
	cd backend && .venv/bin/python -m ruff check .

build:
	cd frontend && npm run build
