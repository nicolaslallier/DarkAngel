# syntax=docker/dockerfile:1

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Installed from the project metadata so the image matches `uv pip install -e .`
# in development; `app` is the package hatchling ships (see pyproject.toml).
COPY pyproject.toml ./
COPY app ./app
# Alembic is not part of the `app` package hatchling ships, so the config and
# the revision scripts are copied in explicitly; WORKDIR is /srv, which is
# where alembic.ini's `script_location = migrations` resolves from.
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install .

RUN useradd --create-home --uid 10001 darkangel
USER darkangel

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status == 200 else 1)"

# Migrate before serving. Safe at one replica, which is what the stack runs;
# spec risk R-4 records the advisory-lock fix for when that changes.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
