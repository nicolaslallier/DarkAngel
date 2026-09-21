# File Management Phase 1 (The Spine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move file metadata into the Infra PostgreSQL and make it the index, with UUID MinIO object keys, upload guards and an audit log — while the user-visible GUI behaves exactly as it does today.

**Architecture:** PostgreSQL holds the metadata and answers every listing; MinIO holds the bytes under opaque `<owner_sub>/<uuid>` keys. Uploads write the database row first as `pending` (which is also the quota reservation), then stream to MinIO, then flip to `ready` — so a file is never listed before its bytes exist. Route handlers talk to a `FileRepository` injected by FastAPI, which lets unit tests substitute an in-memory fake and keeps route logic inside the coverage gate while real SQL stays in the integration suite.

**Tech Stack:** FastAPI (sync handlers), SQLAlchemy 2.0 ORM, Alembic, psycopg 3, MinIO SDK, PostgreSQL 16, Vue 3 + Pinia + TypeScript, pytest, vitest.

**Spec:** `docs/files-feature.md` — read §2 (baseline), §4 (architecture and schema), §6 (business rules) and §11 (phasing) before starting. This plan implements **Phase 1 only**; Phases 2–4 get their own plans.

## Global Constraints

- Python `>=3.11`. The backend virtualenv is `backend/.venv`, created by `uv`. There is no global Python for this project — **always** invoke tools as `.venv/bin/python -m <tool>`.
- ruff, line length **100**, rules `["E", "F", "I", "UP", "B"]`. `make format` fixes, `make format-check` gates.
- Backend tests live in `backend/tests/{unit,integration,regression}/`. **The directory decides the pytest marker** — `tests/conftest.py::pytest_collection_modifyitems` adds it, and a test outside those three directories is a collection error. Never add a marker decorator.
- Frontend tests live in `frontend/tests/{unit,regression}/`.
- Response shapes are pydantic models declared next to their route.
- Settings are read through the cached `get_settings()`, never by instantiating `Settings()`.
- Every route handler is a plain `def` (FastAPI runs it in a threadpool). No `async def` anywhere in this plan.
- Frontend components are `<script setup lang="ts">`; imports use the `@/` alias, never relative paths that climb directories.
- Git: work on a dedicated branch off an up-to-date `origin/main`. Never commit to or push `main`.
- Commit messages end with: `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- The coverage gate is `--cov-fail-under=80` measured over `-m "unit or regression"` only (`.github/workflows/ci.yml:41-44`). Integration coverage does not count toward it.

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `backend/app/core/db.py` | Engine, session factory, `Db` dependency |
| `backend/app/models/__init__.py` | Re-exports `Base` and the four models |
| `backend/app/models/files.py` | All four tables — they change together |
| `backend/app/repositories/__init__.py` | Empty package marker |
| `backend/app/repositories/files.py` | Every owner-scoped query, the audit writer, `FileRepo` dependency |
| `backend/app/scripts/__init__.py` | Empty package marker |
| `backend/app/scripts/backfill.py` | One-shot migration of legacy `<sub>/<name>` objects |
| `backend/alembic.ini` | Alembic config (URL is overridden in `env.py`) |
| `backend/migrations/env.py` | Reads the URL from `Settings` |
| `backend/migrations/script.py.mako` | Alembic's revision template |
| `backend/migrations/versions/0001_initial_schema.py` | Hand-written initial migration |
| `backend/tests/unit/test_db.py` | Session/engine wiring |
| `backend/tests/integration/test_schema.py` | Migration + constraints against real Postgres |
| `backend/tests/integration/test_repository.py` | Repository SQL against real Postgres |
| `scripts/provision-postgres.sh` | Creates the Infra database + scoped role |
| `frontend/src/api/files.ts` | *(rewritten)* id-addressed client |

**Modified**

| File | Change |
|---|---|
| `backend/pyproject.toml` | Three new dependencies |
| `backend/app/core/config.py` | Database URL and upload-guard settings |
| `backend/app/api/routes/files.py` | Rewritten around the repository |
| `backend/tests/conftest.py` | `FakeFileRepository` + `repo` fixture |
| `backend/tests/integration/conftest.py` | `pg_database` session fixture |
| `backend/tests/unit/test_files.py` | Rewritten for the id-addressed API |
| `backend/tests/integration/test_files_minio.py` | Rewritten — its 7 tests all drive the old name-addressed API |
| `backend/tests/regression/test_pr12_unsafe_file_names.py` | Re-pinned to the new invariant |
| `backend/Dockerfile` | Ship `alembic.ini` + `migrations/`, run the upgrade on start |
| `frontend/src/stores/files.ts` | Remove by id |
| `frontend/src/views/FilesView.vue` | Key and act by id |
| `frontend/tests/unit/FilesView.test.ts`, `stores-files.test.ts`, `frontend/tests/regression/pr12-download-blob-url.test.ts` | Updated for ids |
| `docker-compose.test.yml` | Postgres service |
| `Makefile` | `services-test-up/down`, `migrate`, `backfill`, `postgres` |
| `.github/workflows/ci.yml` | Postgres in the integration job |
| `docs/testing.md` | Postgres prerequisite, renamed targets |

---

### Task 1: Dependencies, settings and the database session

**Files:**
- Modify: `backend/pyproject.toml:6-14`
- Modify: `backend/app/core/config.py`
- Create: `backend/app/core/db.py`
- Test: `backend/tests/unit/test_db.py`

**Interfaces:**
- Consumes: `app.core.config.get_settings` (existing).
- Produces: `app.core.db.engine() -> Engine`, `app.core.db.session_factory() -> sessionmaker[Session]`, `app.core.db.db_session() -> Iterator[Session]`, and the annotated dependency `app.core.db.Db`. New settings fields: `database_url: str`, `max_upload_bytes: int`, `user_quota_bytes: int`, `denied_extensions: list[str]`, `inline_content_types: list[str]`.

- [ ] **Step 1: Add the dependencies**

In `backend/pyproject.toml`, extend the `dependencies` list (it currently ends with `"python-multipart>=0.0.9",`):

```toml
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "pydantic>=2.9",
    "pydantic-settings>=2.6",
    "pyjwt[crypto]>=2.10",
    "minio>=7.2",
    "python-multipart>=0.0.9",
    "sqlalchemy>=2.0.30",
    "alembic>=1.13",
    "psycopg[binary]>=3.2",
]
```

- [ ] **Step 2: Install them**

Run: `make install-backend`
Expected: uv resolves and installs sqlalchemy, alembic and psycopg without error.

- [ ] **Step 3: Write the failing test**

Create `backend/tests/unit/test_db.py`:

```python
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import Db, db_session, engine


def test_engine_is_built_from_settings():
    url = engine().url
    assert url.drivername == "postgresql+psycopg"
    assert get_settings().database_url.endswith(f"/{url.database}")


def test_engine_is_cached():
    assert engine() is engine()


def test_db_session_yields_a_session_and_closes_it():
    # Nothing here touches the network: SQLAlchemy connects lazily, on the
    # first statement, and this test never issues one.
    generator = db_session()
    session = next(generator)

    assert isinstance(session, Session)
    assert session.is_active

    generator.close()


def test_db_is_an_annotated_dependency():
    assert Db.__metadata__  # Annotated[Session, Depends(db_session)]
```

- [ ] **Step 4: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.db'`

- [ ] **Step 5: Add the settings fields**

In `backend/app/core/config.py`, insert after the `s3_secret_key` field:

```python
    # Infra PostgreSQL: the metadata index and the source of truth for what
    # files exist. `make postgres` creates the database and its scoped role;
    # the password arrives as a Portainer stack variable, never in git.
    database_url: str = "postgresql+psycopg://darkangel:@postgres:5432/darkangel"

    # Upload guards. Both are refused with 413: one file over the first, or a
    # file that would push the owner's total over the second.
    max_upload_bytes: int = 100 * 1024 * 1024
    user_quota_bytes: int = 5 * 1024 * 1024 * 1024
    # Refused at upload time. Defence in depth only -- the boundary that
    # actually holds is inline_content_types below, which decides what a
    # browser is ever allowed to render inside our own origin.
    denied_extensions: list[str] = [".html", ".htm", ".xhtml", ".svg", ".js", ".mjs"]
    # The only types ever served with `Content-Disposition: inline`. Anything
    # else downloads as an attachment whatever it claims to be, so an uploaded
    # document cannot run script against the SPA's origin.
    inline_content_types: list[str] = [
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
        "application/pdf",
        "text/plain",
    ]
```

- [ ] **Step 6: Write the session module**

Create `backend/app/core/db.py`:

```python
from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


@lru_cache
def engine() -> Engine:
    """One engine per process. `create_engine` opens no connection: the pool
    fills lazily on the first statement, so importing this module is free."""
    settings = get_settings()
    return create_engine(settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)


@lru_cache
def session_factory() -> sessionmaker[Session]:
    # expire_on_commit=False: a route builds its response model from the ORM
    # object *after* the repository commits, and the default would make every
    # attribute access re-query a closed session.
    return sessionmaker(bind=engine(), expire_on_commit=False)


def db_session() -> Iterator[Session]:
    """Request-scoped session. Sync on purpose, like every other dependency
    here: FastAPI runs it in the threadpool, so the blocking driver never
    stalls the event loop."""
    with session_factory()() as session:
        yield session


Db = Annotated[Session, Depends(db_session)]
```

- [ ] **Step 7: Run the test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_db.py -v`
Expected: 4 passed

- [ ] **Step 8: Lint and run the whole fast suite**

Run: `make format && make lint-backend && make test-unit`
Expected: ruff clean; every existing unit test still passes.

- [ ] **Step 9: Commit**

```bash
git add backend/pyproject.toml backend/app/core/config.py backend/app/core/db.py backend/tests/unit/test_db.py
git commit -m "feat(db): add SQLAlchemy engine, session dependency and upload-guard settings

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: A Postgres for the integration suite, and one for Infra

**Files:**
- Modify: `docker-compose.test.yml`
- Modify: `Makefile:163-167` (the `minio-test-up` / `minio-test-down` pair), and `Makefile:154-155` (`test-integration` help text)
- Create: `scripts/provision-postgres.sh`
- Modify: `backend/tests/integration/conftest.py`
- Test: `backend/tests/integration/test_schema.py` (created here with one connectivity test; Task 3 adds the rest)

**Interfaces:**
- Consumes: `app.core.db.engine`, `app.core.db.session_factory` (Task 1).
- Produces: a session-scoped autouse fixture `pg_database` in `backend/tests/integration/conftest.py` that points `DARKANGEL_DATABASE_URL` at the local test Postgres, clears the three relevant `lru_cache`s, and skips locally / fails in CI when the database is unreachable. Make targets `services-test-up`, `services-test-down`, `postgres`.

- [ ] **Step 1: Add Postgres to the test compose file**

In `docker-compose.test.yml`, add a second service under `services:` (after the existing `minio:` block) and update the file's header comment:

```yaml
  # Postgres 16, because the schema uses `UNIQUE NULLS NOT DISTINCT` (PG 15+).
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: darkangel
      POSTGRES_PASSWORD: darkangel
      POSTGRES_DB: darkangel
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U darkangel -d darkangel"]
      interval: 2s
      timeout: 3s
      retries: 20
```

- [ ] **Step 2: Rename the compose make targets**

The file now starts two services, so the names must stop saying "minio". Replace `Makefile:163-167`:

```make
services-test-up: ## Start the services the integration suite runs against (MinIO + Postgres)
	docker compose -f docker-compose.test.yml up -d --wait

services-test-down: ## Stop those services and drop their data
	docker compose -f docker-compose.test.yml down -v
```

And update the `test-integration` help text on `Makefile:154`:

```make
test-integration: $(PY) ## Backend integration tests (needs MinIO + Postgres; `make services-test-up`)
```

- [ ] **Step 3: Start the services**

Run: `make services-test-up`
Expected: both containers report healthy; `docker compose -f docker-compose.test.yml ps` shows `minio` and `postgres` as running.

- [ ] **Step 4: Write the failing connectivity test**

Create `backend/tests/integration/test_schema.py`:

```python
"""Integration — the schema, against a real PostgreSQL.

Everything here needs SQL that SQLite cannot run (uuid, text[], jsonb, partial
indexes with NULLS NOT DISTINCT), which is why none of it is a unit test.
"""

from sqlalchemy import text


def test_the_database_is_reachable(db):
    assert db.scalar(text("SELECT 1")) == 1


def test_it_is_postgres_15_or_newer(db):
    # `UNIQUE NULLS NOT DISTINCT` in migration 0001 needs 15+.
    major = db.scalar(text("SHOW server_version_num"))
    assert int(major) >= 150000
```

- [ ] **Step 5: Run it to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_schema.py -v`
Expected: FAIL — `fixture 'db' not found`

- [ ] **Step 6: Add the Postgres fixtures**

Append to `backend/tests/integration/conftest.py`:

```python
# --- PostgreSQL -----------------------------------------------------------
# Same shape as minio_bucket above: default the environment, clear the caches
# that read it, and fail rather than skip when CI is the one running.

PG_DEFAULTS = {
    "DARKANGEL_DATABASE_URL": "postgresql+psycopg://darkangel:darkangel@localhost:5432/darkangel",
}


@pytest.fixture(scope="session", autouse=True)
def pg_database():
    """Point the app at the local test Postgres and migrate it to head.

    Ordering against `minio_bucket` does not matter: both fixtures write to
    os.environ, which is the single source of truth, and both clear
    `get_settings` afterwards. What *does* matter is clearing the two db
    caches -- an engine built before DARKANGEL_DATABASE_URL was set would
    point at the unreachable production host for the rest of the session.
    """
    prior = {key: os.environ.get(key) for key in PG_DEFAULTS}

    def restore():
        for key, value in prior.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()
        db_module.engine.cache_clear()
        db_module.session_factory.cache_clear()

    try:
        for key, value in PG_DEFAULTS.items():
            os.environ.setdefault(key, value)

        get_settings.cache_clear()
        db_module.engine.cache_clear()
        db_module.session_factory.cache_clear()

        url = get_settings().database_url
        try:
            with db_module.engine().connect() as connection:
                connection.execute(sa_text("SELECT 1"))
        except Exception as e:
            reason = f"PostgreSQL unreachable at {url.rsplit('@', 1)[-1]}: {e}"
            if os.environ.get("CI") == "true":
                pytest.fail(reason, pytrace=False)
            pytest.skip(reason)

        alembic_config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
        command.upgrade(alembic_config, "head")

        yield url
    finally:
        restore()


@pytest.fixture
def db(pg_database):
    """A session per test, with every table emptied first.

    TRUNCATE, not DELETE: audit_log carries a BEFORE DELETE trigger that makes
    it append-only, and TRUNCATE does not fire row triggers.
    """
    with db_module.session_factory()() as session:
        session.execute(
            sa_text("TRUNCATE audit_log, file_versions, files, folders RESTART IDENTITY CASCADE")
        )
        session.commit()
        yield session
```

and extend the imports at the top of that file:

```python
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text as sa_text

from app.api.routes import files
from app.core import db as db_module
from app.core.config import get_settings
```

- [ ] **Step 7: Run the test — it should now fail for a new reason**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_schema.py -v`
Expected: FAIL — alembic cannot find `alembic.ini`. That file arrives in Task 3; this task's deliverable is the service and the fixture wiring.

- [ ] **Step 8: Write the Infra provisioning script**

Create `scripts/provision-postgres.sh` (mirroring `scripts/provision-minio.sh`, which is the reference for how this repo reads `INFRA_ENV` and `.portainer.env` — read it first and match its conventions exactly):

```bash
#!/usr/bin/env bash
# Create/update the DarkAngel database and its scoped role in the Infra
# PostgreSQL. Idempotent: safe to re-run, and re-running resets the password.
#
#   database  darkangel
#   role      darkangel  -- owns that database and nothing else
#
# Needs an admin connection to the Infra Postgres in PGADMIN_URL, and writes
# the generated password back into .portainer.env as POSTGRES_PASSWORD, the
# same way provision-minio.sh handles MINIO_SECRET_KEY.
set -euo pipefail

: "${PGADMIN_URL:?set PGADMIN_URL to an admin connection string for the Infra Postgres}"
ENV_FILE="${ENV_FILE:-.portainer.env}"

password="$(openssl rand -base64 30 | tr -d '/+=' | head -c 32)"

psql "$PGADMIN_URL" --quiet --no-psqlrc --set ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'darkangel') THEN
    CREATE ROLE darkangel LOGIN PASSWORD '${password}';
  ELSE
    ALTER ROLE darkangel LOGIN PASSWORD '${password}';
  END IF;
END
\$\$;
SQL

# CREATE DATABASE cannot run inside the DO block above.
if ! psql "$PGADMIN_URL" -tAc "SELECT 1 FROM pg_database WHERE datname='darkangel'" | grep -q 1; then
  psql "$PGADMIN_URL" --quiet --set ON_ERROR_STOP=1 \
    -c "CREATE DATABASE darkangel OWNER darkangel"
fi

if grep -q '^POSTGRES_PASSWORD=' "$ENV_FILE" 2>/dev/null; then
  # BSD and GNU sed disagree on -i; rewrite through a temp file instead.
  tmp="$(mktemp)"
  sed "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${password}|" "$ENV_FILE" >"$tmp"
  mv "$tmp" "$ENV_FILE"
else
  echo "POSTGRES_PASSWORD=${password}" >>"$ENV_FILE"
fi

echo "provision-postgres.sh: database darkangel + role darkangel ready; password written to $ENV_FILE"
```

Then `chmod +x scripts/provision-postgres.sh` and add the target next to `minio:` in the Makefile (around `Makefile:248`):

```make
postgres: ## Create/update the Infra PostgreSQL database + role the metadata lives in (PGADMIN_URL)
	@scripts/provision-postgres.sh
```

- [ ] **Step 9: Verify the script's shell is valid**

Run: `bash -n scripts/provision-postgres.sh && shellcheck scripts/provision-postgres.sh || true`
Expected: `bash -n` exits 0. This script cannot be unit-tested — it targets the real Infra Postgres — so verifying syntax and reviewing it by eye is the check. Running it for real is a deployment step, not part of this task.

- [ ] **Step 10: Commit**

```bash
git add docker-compose.test.yml Makefile scripts/provision-postgres.sh backend/tests/integration/conftest.py backend/tests/integration/test_schema.py
git commit -m "test(db): run integration suites against a real Postgres; provision the Infra one

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: ORM models and the initial migration

**Files:**
- Create: `backend/app/models/__init__.py`, `backend/app/models/files.py`
- Create: `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako`, `backend/migrations/versions/0001_initial_schema.py`
- Modify: `Makefile` (add `migrate`)
- Test: `backend/tests/integration/test_schema.py`

**Interfaces:**
- Consumes: `app.core.config.get_settings`, `app.core.db.engine` (Tasks 1–2).
- Produces: `app.models.files.Base`, and the models `Folder`, `File`, `FileVersion`, `AuditLog`. `File` fields used by every later task: `id: uuid.UUID`, `owner_sub: str`, `folder_id: uuid.UUID | None`, `name: str`, `description: str | None`, `tags: list[str]`, `content_type: str`, `size_bytes: int`, `object_key: str`, `status: str`, `current_version_id: uuid.UUID | None`, `created_at/updated_at: datetime`, `deleted_at: datetime | None`. `FileVersion` fields: `id`, `file_id`, `version_no: int`, `s3_version_id: str`, `size_bytes: int`, `content_type: str`, `created_at`, `created_by: str`.

- [ ] **Step 1: Write the failing schema tests**

Replace the body of `backend/tests/integration/test_schema.py` (keep the two tests from Task 2 and the docstring, add these):

```python
import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.core.db import engine
from app.models.files import AuditLog, File, FileVersion, Folder


def test_every_table_exists(db):
    tables = set(inspect(engine()).get_table_names())
    assert {"folders", "files", "file_versions", "audit_log"} <= tables


def test_two_root_folders_cannot_share_a_name(db):
    # A plain UNIQUE would let this through: Postgres treats NULL parents as
    # distinct. The partial index in 0001 is declared NULLS NOT DISTINCT.
    db.add(Folder(id=uuid.uuid4(), owner_sub="user-1", parent_id=None, name="work"))
    db.commit()

    db.add(Folder(id=uuid.uuid4(), owner_sub="user-1", parent_id=None, name="work"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_two_owners_may_each_have_a_root_folder_named_work(db):
    db.add(Folder(id=uuid.uuid4(), owner_sub="user-1", parent_id=None, name="work"))
    db.add(Folder(id=uuid.uuid4(), owner_sub="user-2", parent_id=None, name="work"))
    db.commit()

    assert db.query(Folder).count() == 2


def _file(owner="user-1", name="a.txt", status="ready", **kw):
    file_id = kw.pop("id", uuid.uuid4())
    return File(
        id=file_id,
        owner_sub=owner,
        name=name,
        content_type="text/plain",
        size_bytes=5,
        object_key=f"{owner}/{file_id}",
        status=status,
        **kw,
    )


def test_tags_default_to_an_empty_array(db):
    row = _file()
    db.add(row)
    db.commit()
    db.refresh(row)

    assert row.tags == []


def test_two_ready_files_cannot_share_a_name_in_one_folder(db):
    db.add(_file(name="notes.txt"))
    db.commit()

    db.add(_file(name="notes.txt"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_a_pending_file_does_not_block_the_name(db):
    # The unique index covers ready rows only, so a stale pending upload can
    # never lock a user out of a name.
    db.add(_file(name="notes.txt", status="pending"))
    db.commit()

    db.add(_file(name="notes.txt", status="ready"))
    db.commit()

    assert db.query(File).count() == 2


def test_deleting_a_file_cascades_to_its_versions(db):
    row = _file()
    db.add(row)
    db.commit()
    db.add(
        FileVersion(
            id=uuid.uuid4(),
            file_id=row.id,
            version_no=1,
            s3_version_id="v1",
            size_bytes=5,
            content_type="text/plain",
            created_by="user-1",
        )
    )
    db.commit()

    db.delete(row)
    db.commit()

    assert db.query(FileVersion).count() == 0


def test_audit_log_rows_cannot_be_updated_or_deleted(db):
    entry = AuditLog(
        actor_sub="user-1", action="upload", target_type="file", target_id=uuid.uuid4()
    )
    db.add(entry)
    db.commit()

    with pytest.raises(ProgrammingError):
        db.execute(text("UPDATE audit_log SET action = 'tampered'"))
    db.rollback()

    with pytest.raises(ProgrammingError):
        db.execute(text("DELETE FROM audit_log"))
    db.rollback()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Write the models**

Create `backend/app/models/files.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every DarkAngel table."""


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_sub: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("folders.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class File(Base):
    __tablename__ = "files"

    # Generated in Python, not by the database: the upload path needs the id
    # before the row exists, because the id *is* the object key it uploads to.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_sub: Mapped[str] = mapped_column(Text, nullable=False)
    folder_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("folders.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    # Deliberately not a foreign key: files -> file_versions -> files is a
    # cycle, and the ON DELETE CASCADE on file_versions.file_id already keeps
    # the two in step. A constraint here would only need `use_alter` ceremony.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FileVersion(Base):
    __tablename__ = "file_versions"
    __table_args__ = (UniqueConstraint("file_id", "version_no", name="uq_file_versions_no"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    s3_version_id: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str] = mapped_column(Text, nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor_sub: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

Create `backend/app/models/__init__.py`:

```python
from app.models.files import AuditLog, Base, File, FileVersion, Folder

__all__ = ["AuditLog", "Base", "File", "FileVersion", "Folder"]
```

- [ ] **Step 4: Write the Alembic scaffolding**

Create `backend/alembic.ini`:

```ini
# The URL is deliberately absent: migrations/env.py reads it from Settings, so
# there is exactly one place the connection string comes from.
[alembic]
script_location = migrations
prepend_sys_path = .

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

Create `backend/migrations/env.py`:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.core.config import get_settings
from app.models.files import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # create_engine directly, not engine_from_config: a password containing a
    # '%' would be mangled by alembic.ini's ConfigParser interpolation.
    connectable = create_engine(get_settings().database_url, poolclass=None)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Create `backend/migrations/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 5: Write the initial migration by hand**

Create `backend/migrations/versions/0001_initial_schema.py`. It is hand-written, not autogenerated, because three of its objects (the `NULLS NOT DISTINCT` partial indexes and the append-only trigger) cannot be expressed in the ORM metadata — `--autogenerate` would propose dropping them on every later revision.

```python
"""Initial schema: folders, files, file_versions, audit_log.

Hand-written. Autogenerate cannot express the NULLS NOT DISTINCT partial
indexes or the append-only trigger below, and would offer to drop them on
every subsequent revision.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_sub", sa.Text(), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("folders.id")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_folders_owner_sub", "folders", ["owner_sub"])

    op.create_table(
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_sub", sa.Text(), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("folders.id")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "file_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("s3_version_id", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.UniqueConstraint("file_id", "version_no", name="uq_file_versions_no"),
    )
    op.create_index("ix_file_versions_file_id", "file_versions", ["file_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor_sub", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("detail", postgresql.JSONB()),
    )
    op.create_index("ix_audit_log_actor_sub", "audit_log", ["actor_sub"])
    op.create_index("ix_audit_log_target_id", "audit_log", ["target_id"])

    # NULLS NOT DISTINCT (PG 15+): without it, two root folders -- both with a
    # NULL parent_id -- could share a name, because Postgres normally treats
    # every NULL as different from every other NULL.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_folders_sibling_name
            ON folders (owner_sub, parent_id, lower(name))
            NULLS NOT DISTINCT
            WHERE deleted_at IS NULL
        """
    )

    # Ready files only: a stale pending upload must never lock a user out of a
    # name, and a trashed file must not either.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_files_folder_name
            ON files (owner_sub, folder_id, lower(name))
            NULLS NOT DISTINCT
            WHERE deleted_at IS NULL AND status = 'ready'
        """
    )

    # The listing query: owner + folder, newest first.
    op.execute(
        """
        CREATE INDEX ix_files_listing
            ON files (owner_sub, folder_id, updated_at DESC)
            WHERE deleted_at IS NULL AND status = 'ready'
        """
    )
    op.execute("CREATE INDEX ix_files_tags ON files USING GIN (tags)")
    op.execute("CREATE INDEX ix_files_pending ON files (created_at) WHERE status = 'pending'")

    # Append-only, enforced by the database rather than by convention. A
    # trigger rather than a REVOKE, because the application role owns these
    # tables and could simply grant the privilege back to itself.
    op.execute(
        """
        CREATE FUNCTION audit_log_is_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_change
            BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_is_append_only()")
    op.drop_table("audit_log")
    op.drop_table("file_versions")
    op.drop_table("files")
    op.drop_table("folders")
```

- [ ] **Step 6: Add the migrate target**

Add to the Makefile next to the other backend targets:

```make
migrate: $(PY) ## Apply pending Alembic migrations to DARKANGEL_DATABASE_URL
	cd $(BACKEND) && .venv/bin/python -m alembic upgrade head
```

- [ ] **Step 7: Run the schema tests to verify they pass**

Run: `make services-test-up && cd backend && .venv/bin/python -m pytest tests/integration/test_schema.py -v`
Expected: 10 passed. The `pg_database` fixture runs `alembic upgrade head` itself, so no manual migration step is needed.

- [ ] **Step 8: Verify the migration reverses cleanly**

Run: `cd backend && DARKANGEL_DATABASE_URL="postgresql+psycopg://darkangel:darkangel@localhost:5432/darkangel" .venv/bin/python -m alembic downgrade base && .venv/bin/python -m alembic upgrade head`
Expected: both complete without error. A migration that cannot be reversed is a migration that cannot be rolled back in production.

- [ ] **Step 9: Lint and commit**

```bash
make format && make lint-backend
git add backend/app/models backend/alembic.ini backend/migrations backend/tests/integration/test_schema.py Makefile
git commit -m "feat(db): add the file metadata schema and its initial migration

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The repository, the audit writer, and the fake for unit tests

**Files:**
- Create: `backend/app/repositories/__init__.py`, `backend/app/repositories/files.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/integration/test_repository.py`

**Interfaces:**
- Consumes: `app.core.db.Db`, `app.models.files.{AuditLog, File, FileVersion}`.
- Produces: `QuotaExceeded(used, limit, needed)`; `FileRepository` with methods `list(owner_sub, *, limit, offset) -> Sequence[File]`, `get(owner_sub, file_id) -> File | None`, `find_by_name(owner_sub, name, folder_id) -> File | None`, `reserve(owner_sub, *, name, folder_id, size_bytes, content_type, quota_bytes) -> File`, `finalize(file, *, s3_version_id, actor_sub) -> File`, `add_version(file, *, s3_version_id, size_bytes, content_type, actor_sub) -> File`, `abandon(file) -> None`, `soft_delete(file) -> None`, `sweep_pending(older_than_seconds=3600) -> list[str]`, `audit(actor_sub, action, target_type, target_id, detail=None) -> None`, `used_bytes(owner_sub) -> int`. Plus the dependency `file_repository(db)` and `FileRepo = Annotated[FileRepository, Depends(file_repository)]`. Unit tests get the `repo` fixture returning a `FakeFileRepository` with the same surface.

**Why a class here:** it is the one abstraction this plan adds, and it pays for itself twice — the fake keeps route logic (status codes, headers, guards) inside the `unit or regression` coverage gate, and making `owner_sub` the first argument of every method turns spec risk R-1 into something a reviewer can check at a glance.

- [ ] **Step 1: Write the failing repository tests**

Create `backend/tests/integration/test_repository.py`:

```python
"""Integration — the repository's SQL, against a real PostgreSQL."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.files import AuditLog, File
from app.repositories.files import FileRepository, QuotaExceeded

QUOTA = 1000


@pytest.fixture
def repository(db):
    return FileRepository(db)


def reserve(repository, owner="user-1", name="a.txt", size=10, quota=QUOTA):
    return repository.reserve(
        owner,
        name=name,
        folder_id=None,
        size_bytes=size,
        content_type="text/plain",
        quota_bytes=quota,
    )


def test_reserve_creates_a_pending_row_keyed_by_its_own_id(repository):
    row = reserve(repository)

    assert row.status == "pending"
    assert row.object_key == f"user-1/{row.id}"


def test_pending_rows_are_not_listed(repository):
    reserve(repository)

    assert repository.list("user-1") == []


def test_finalize_makes_the_file_visible_with_version_one(repository, db):
    row = reserve(repository)

    repository.finalize(row, s3_version_id="v-abc", actor_sub="user-1")

    listed = repository.list("user-1")
    assert [f.id for f in listed] == [row.id]
    assert row.status == "ready"
    version = db.query(File).get(row.id).current_version_id
    assert version is not None


def test_list_and_get_never_cross_owners(repository):
    row = reserve(repository, owner="user-1")
    repository.finalize(row, s3_version_id="v", actor_sub="user-1")

    assert repository.list("user-2") == []
    assert repository.get("user-2", row.id) is None
    assert repository.get("user-1", row.id).id == row.id


def test_pending_rows_reserve_quota(repository):
    # 900 pending + 200 more would exceed 1000, even though nothing is ready.
    reserve(repository, name="big.txt", size=900)

    with pytest.raises(QuotaExceeded) as caught:
        reserve(repository, name="more.txt", size=200)

    assert caught.value.used == 900
    assert caught.value.limit == QUOTA
    assert caught.value.needed == 200


def test_a_file_exactly_filling_the_quota_is_allowed(repository):
    reserve(repository, size=QUOTA)

    assert repository.used_bytes("user-1") == QUOTA


def test_quota_is_per_owner(repository):
    reserve(repository, owner="user-1", size=900)
    reserve(repository, owner="user-2", size=900)

    assert repository.used_bytes("user-1") == 900
    assert repository.used_bytes("user-2") == 900


def test_abandon_removes_the_reservation(repository):
    row = reserve(repository, size=900)

    repository.abandon(row)

    assert repository.used_bytes("user-1") == 0


def test_soft_delete_hides_the_file_but_keeps_the_row(repository, db):
    row = reserve(repository)
    repository.finalize(row, s3_version_id="v", actor_sub="user-1")

    repository.soft_delete(row)

    assert repository.list("user-1") == []
    assert repository.get("user-1", row.id) is None
    assert db.query(File).count() == 1


def test_add_version_increments_and_resizes(repository):
    row = reserve(repository, size=10)
    repository.finalize(row, s3_version_id="v1", actor_sub="user-1")

    repository.add_version(
        row, s3_version_id="v2", size_bytes=40, content_type="text/plain", actor_sub="user-1"
    )

    assert row.size_bytes == 40
    assert repository.used_bytes("user-1") == 40


def test_find_by_name_matches_only_live_ready_files(repository):
    row = reserve(repository, name="notes.txt")
    assert repository.find_by_name("user-1", "notes.txt", None) is None  # still pending

    repository.finalize(row, s3_version_id="v", actor_sub="user-1")
    assert repository.find_by_name("user-1", "notes.txt", None).id == row.id

    repository.soft_delete(row)
    assert repository.find_by_name("user-1", "notes.txt", None) is None


def test_sweep_pending_only_takes_stale_rows(repository, db):
    fresh = reserve(repository, name="fresh.txt")
    stale = reserve(repository, name="stale.txt")
    db.execute(
        text("UPDATE files SET created_at = :old WHERE id = :id"),
        {"old": datetime.now(UTC) - timedelta(hours=2), "id": stale.id},
    )
    db.commit()

    swept = repository.sweep_pending()

    assert swept == [f"user-1/{stale.id}"]
    assert db.query(File).count() == 1
    assert db.query(File).one().id == fresh.id


def test_audit_writes_one_row(repository, db):
    target = uuid.uuid4()

    repository.audit("user-1", "upload", "file", target, {"name": "a.txt"})

    entry = db.query(AuditLog).one()
    assert (entry.actor_sub, entry.action, entry.target_id) == ("user-1", "upload", target)
    assert entry.detail == {"name": "a.txt"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.repositories'`

- [ ] **Step 3: Write the repository**

Create `backend/app/repositories/__init__.py` (empty file), and `backend/app/repositories/files.py`:

```python
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.files import AuditLog, File, FileVersion


class QuotaExceeded(Exception):
    def __init__(self, used: int, limit: int, needed: int) -> None:
        self.used, self.limit, self.needed = used, limit, needed
        super().__init__(f"{used} of {limit} bytes used, {needed} more needed")


class FileRepository:
    """Every query is scoped to one owner, and `owner_sub` is always the first
    argument so that omitting it is a TypeError rather than a data leak.

    This is the only thing separating two users now that the MinIO key prefix
    no longer does it structurally -- see docs/files-feature.md risk R-1.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- reads ---

    def list(self, owner_sub: str, *, limit: int = 100, offset: int = 0) -> Sequence[File]:
        statement = (
            select(File)
            .where(
                File.owner_sub == owner_sub,
                File.deleted_at.is_(None),
                File.status == "ready",
            )
            .order_by(File.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return self.db.scalars(statement).all()

    def get(self, owner_sub: str, file_id: uuid.UUID) -> File | None:
        statement = select(File).where(
            File.id == file_id,
            File.owner_sub == owner_sub,
            File.deleted_at.is_(None),
            File.status == "ready",
        )
        return self.db.scalars(statement).one_or_none()

    def find_by_name(
        self, owner_sub: str, name: str, folder_id: uuid.UUID | None
    ) -> File | None:
        statement = select(File).where(
            File.owner_sub == owner_sub,
            func.lower(File.name) == name.lower(),
            File.folder_id.is_(folder_id) if folder_id is None else File.folder_id == folder_id,
            File.deleted_at.is_(None),
            File.status == "ready",
        )
        return self.db.scalars(statement).one_or_none()

    def used_bytes(self, owner_sub: str) -> int:
        # Pending rows count: the pending insert is the quota reservation.
        statement = select(func.coalesce(func.sum(File.size_bytes), 0)).where(
            File.owner_sub == owner_sub, File.deleted_at.is_(None)
        )
        return self.db.scalar(statement) or 0

    # --- writes ---

    def reserve(
        self,
        owner_sub: str,
        *,
        name: str,
        folder_id: uuid.UUID | None,
        size_bytes: int,
        content_type: str,
        quota_bytes: int,
    ) -> File:
        """Check the quota and claim the space, in one short transaction.

        The advisory lock serialises concurrent uploads by the same owner, so
        two requests cannot both read an under-quota total and both insert. It
        is transaction-scoped and released at the commit below -- long before
        the caller starts streaming bytes to MinIO.
        """
        self.db.execute(select(func.pg_advisory_xact_lock(func.hashtext(owner_sub))))

        used = self.used_bytes(owner_sub)
        if used + size_bytes > quota_bytes:
            self.db.rollback()
            raise QuotaExceeded(used, quota_bytes, size_bytes)

        file_id = uuid.uuid4()
        row = File(
            id=file_id,
            owner_sub=owner_sub,
            folder_id=folder_id,
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            object_key=f"{owner_sub}/{file_id}",
            status="pending",
        )
        self.db.add(row)
        self.db.commit()
        return row

    def finalize(self, file: File, *, s3_version_id: str, actor_sub: str) -> File:
        version = FileVersion(
            id=uuid.uuid4(),
            file_id=file.id,
            version_no=1,
            s3_version_id=s3_version_id,
            size_bytes=file.size_bytes,
            content_type=file.content_type,
            created_by=actor_sub,
        )
        self.db.add(version)
        file.status = "ready"
        file.current_version_id = version.id
        self.db.commit()
        return file

    def add_version(
        self,
        file: File,
        *,
        s3_version_id: str,
        size_bytes: int,
        content_type: str,
        actor_sub: str,
    ) -> File:
        highest = (
            self.db.scalar(
                select(func.max(FileVersion.version_no)).where(FileVersion.file_id == file.id)
            )
            or 0
        )
        version = FileVersion(
            id=uuid.uuid4(),
            file_id=file.id,
            version_no=highest + 1,
            s3_version_id=s3_version_id,
            size_bytes=size_bytes,
            content_type=content_type,
            created_by=actor_sub,
        )
        self.db.add(version)
        file.size_bytes = size_bytes
        file.content_type = content_type
        file.current_version_id = version.id
        file.updated_at = datetime.now(UTC)
        self.db.commit()
        return file

    def abandon(self, file: File) -> None:
        self.db.delete(file)
        self.db.commit()

    def soft_delete(self, file: File) -> None:
        file.deleted_at = datetime.now(UTC)
        self.db.commit()

    def sweep_pending(self, older_than_seconds: int = 3600) -> list[str]:
        """Drop reservations whose upload never finished, returning the object
        keys the caller should try to delete from MinIO."""
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        stale = self.db.scalars(
            select(File).where(File.status == "pending", File.created_at < cutoff)
        ).all()
        keys = [row.object_key for row in stale]
        for row in stale:
            self.db.delete(row)
        self.db.commit()
        return keys

    def audit(
        self,
        actor_sub: str,
        action: str,
        target_type: str,
        target_id: uuid.UUID,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            AuditLog(
                actor_sub=actor_sub,
                action=action,
                target_type=target_type,
                target_id=target_id,
                detail=detail,
            )
        )
        self.db.commit()


def file_repository(db: Db) -> FileRepository:
    return FileRepository(db)


FileRepo = Annotated[FileRepository, Depends(file_repository)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_repository.py -v`
Expected: 14 passed

- [ ] **Step 5: Add the in-memory fake for unit tests**

Append to `backend/tests/conftest.py`, after the `FakeMinio` class:

```python
class FakeFileRepository:
    """The slice of FileRepository the routes use, over a list.

    It stores real `File` model objects: unattached to a session they are just
    data holders, so the fake cannot drift from the real column names.
    """

    def __init__(self):
        self.rows: list[File] = []
        self.versions: dict[uuid.UUID, int] = {}
        self.audits: list[tuple] = []
        self.swept: list[str] = []

    def _live(self, owner_sub):
        return [
            r
            for r in self.rows
            if r.owner_sub == owner_sub and r.deleted_at is None and r.status == "ready"
        ]

    def list(self, owner_sub, *, limit=100, offset=0):
        return self._live(owner_sub)[offset : offset + limit]

    def get(self, owner_sub, file_id):
        return next((r for r in self._live(owner_sub) if r.id == file_id), None)

    def find_by_name(self, owner_sub, name, folder_id):
        return next(
            (
                r
                for r in self._live(owner_sub)
                if r.name.lower() == name.lower() and r.folder_id == folder_id
            ),
            None,
        )

    def used_bytes(self, owner_sub):
        return sum(r.size_bytes for r in self.rows if r.owner_sub == owner_sub and not r.deleted_at)

    def reserve(self, owner_sub, *, name, folder_id, size_bytes, content_type, quota_bytes):
        used = self.used_bytes(owner_sub)
        if used + size_bytes > quota_bytes:
            raise QuotaExceeded(used, quota_bytes, size_bytes)
        file_id = uuid.uuid4()
        row = File(
            id=file_id,
            owner_sub=owner_sub,
            folder_id=folder_id,
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            object_key=f"{owner_sub}/{file_id}",
            status="pending",
            tags=[],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.rows.append(row)
        return row

    def finalize(self, file, *, s3_version_id, actor_sub):
        file.status = "ready"
        file.current_version_id = uuid.uuid4()
        self.versions[file.id] = 1
        return file

    def add_version(self, file, *, s3_version_id, size_bytes, content_type, actor_sub):
        self.versions[file.id] = self.versions.get(file.id, 1) + 1
        file.size_bytes = size_bytes
        file.content_type = content_type
        file.updated_at = datetime.now(UTC)
        return file

    def abandon(self, file):
        self.rows.remove(file)

    def soft_delete(self, file):
        file.deleted_at = datetime.now(UTC)

    def sweep_pending(self, older_than_seconds=3600):
        return self.swept

    def audit(self, actor_sub, action, target_type, target_id, detail=None):
        self.audits.append((actor_sub, action, target_type, target_id, detail))


@pytest.fixture
def repo():
    """Swap the repository for the in-memory fake. Explicit, never autouse:
    the integration suite must keep talking to the real database."""
    fake = FakeFileRepository()
    app.dependency_overrides[file_repository] = lambda: fake
    yield fake
    app.dependency_overrides.pop(file_repository, None)
```

and extend that file's imports:

```python
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from textwrap import shorten
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from minio.error import S3Error

from app.api.routes import files
from app.core import auth
from app.main import app
from app.models.files import File
from app.repositories.files import QuotaExceeded, file_repository
```

- [ ] **Step 6: Guard the integration suite against the fake**

`backend/tests/integration/conftest.py` already shadows the `store` fixture so integration tests cannot silently use `FakeMinio`. Add the same guard for the repository, right after that `store` fixture:

```python
@pytest.fixture
def repo():
    """Shadow the shared `repo` fixture for the same reason as `store`: this
    suite exists to exercise real SQL, and the fake would quietly defeat it."""
    pytest.fail(
        "integration tests run against real PostgreSQL; the FakeFileRepository "
        "`repo` fixture is unit/regression only"
    )
```

- [ ] **Step 7: Verify both suites still collect and pass**

Run: `make test-unit && cd backend && .venv/bin/python -m pytest -m integration -v`
Expected: unit passes unchanged (nothing uses `repo` yet); integration passes 16.

- [ ] **Step 8: Lint and commit**

```bash
make format && make lint-backend
git add backend/app/repositories backend/tests/conftest.py backend/tests/integration/conftest.py backend/tests/integration/test_repository.py
git commit -m "feat(files): add the owner-scoped repository, audit writer and its test fake

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Read the list and one file from Postgres

**Files:**
- Modify: `backend/app/api/routes/files.py` (replace `FileInfo`, `_prefix`, `_key` and `list_files`)
- Test: `backend/tests/unit/test_files.py` (replace the list/ownership tests)

**Interfaces:**
- Consumes: `FileRepo`, `FileRepository.list`, `.get`, `.sweep_pending` (Task 4).
- Produces: the response model `FileInfo` with fields `id: UUID`, `name: str`, `size: int`, `content_type: str`, `modified: datetime | None` and the constructor `FileInfo.of(row: File) -> FileInfo`. Routes `GET /api/files` and `GET /api/files/{file_id}`.

- [ ] **Step 1: Write the failing tests**

Replace the whole of `backend/tests/unit/test_files.py` with the read-path tests (the write-path ones come back in Task 6):

```python
import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def seed(repo, name="a.txt", sub="user-1", size=5, content_type="text/plain"):
    """A ready file straight in the fake, so read tests do not depend on upload."""
    row = repo.reserve(
        sub,
        name=name,
        folder_id=None,
        size_bytes=size,
        content_type=content_type,
        quota_bytes=10**9,
    )
    repo.finalize(row, s3_version_id="v1", actor_sub=sub)
    return row


def test_list_returns_the_owners_ready_files(repo):
    row = seed(repo, name="bail été.txt")

    listed = client.get("/api/files", headers=auth()).json()

    assert listed == [
        {
            "id": str(row.id),
            "name": "bail été.txt",
            "size": 5,
            "content_type": "text/plain",
            "modified": row.updated_at.isoformat(),
        }
    ]


def test_list_excludes_other_owners(repo):
    seed(repo, name="secret.txt", sub="user-1")

    assert client.get("/api/files", headers=auth("user-2")).json() == []


def test_list_excludes_pending_uploads(repo):
    repo.reserve(
        "user-1",
        name="half.txt",
        folder_id=None,
        size_bytes=5,
        content_type="text/plain",
        quota_bytes=10**9,
    )

    assert client.get("/api/files", headers=auth()).json() == []


def test_get_returns_one_file(repo):
    row = seed(repo)

    response = client.get(f"/api/files/{row.id}", headers=auth())

    assert response.status_code == 200
    assert response.json()["id"] == str(row.id)


def test_get_hides_another_owners_file_behind_404(repo):
    row = seed(repo, sub="user-1")

    # 404, not 403: a 403 would confirm the id exists.
    assert client.get(f"/api/files/{row.id}", headers=auth("user-2")).status_code == 404


def test_get_rejects_a_malformed_id(repo):
    assert client.get("/api/files/not-a-uuid", headers=auth()).status_code == 422


def test_get_returns_404_for_an_unknown_id(repo):
    assert client.get(f"/api/files/{uuid.uuid4()}", headers=auth()).status_code == 404


def test_the_routes_need_a_token(repo):
    row = seed(repo)

    assert client.get("/api/files").status_code == 401
    assert client.get(f"/api/files/{row.id}").status_code == 401


def test_listing_sweeps_abandoned_reservations(repo, store):
    store.objects["user-1/orphan"] = (b"x", "text/plain")
    repo.swept = ["user-1/orphan"]

    client.get("/api/files", headers=auth())

    assert store.objects == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v`
Expected: FAIL — the responses have no `id`, and `/api/files/{uuid}` is treated as a filename.

- [ ] **Step 3: Rewrite the read half of the route module**

In `backend/app/api/routes/files.py`, replace the imports, the `FileInfo` model and `list_files`, and delete `_prefix`. Leave `upload_file` for now — Task 6 replaces it — and read the note after the code block about the other two routes.

```python
import os
import uuid
from collections.abc import Iterator
from contextlib import suppress
from datetime import datetime
from functools import lru_cache
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel

from app.core.auth import Claims
from app.core.config import get_settings
from app.models.files import File
from app.repositories.files import FileRepo

router = APIRouter(prefix="/files", tags=["files"])


class FileInfo(BaseModel):
    id: uuid.UUID
    name: str
    size: int
    content_type: str
    modified: datetime | None

    @classmethod
    def of(cls, row: File) -> "FileInfo":
        return cls(
            id=row.id,
            name=row.name,
            size=row.size_bytes,
            content_type=row.content_type,
            modified=row.updated_at,
        )


@lru_cache
def minio_client() -> Minio:
    s = get_settings()
    return Minio(
        s.s3_endpoint, access_key=s.s3_access_key, secret_key=s.s3_secret_key, secure=s.s3_secure
    )


def _sweep(repo: FileRepo) -> None:
    """Drop reservations whose upload never finished, and the bytes they may
    have left behind."""
    bucket = get_settings().s3_bucket
    for key in repo.sweep_pending():
        with suppress(S3Error):
            minio_client().remove_object(bucket, key)


@router.get("", response_model=list[FileInfo])
def list_files(
    claims: Claims,
    repo: FileRepo,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[FileInfo]:
    # There is no scheduler in this stack, so the sweep rides along here. It is
    # a single indexed DELETE over a table that is almost always empty.
    # ponytail: inline sweep; move to a cron if list latency ever suffers
    _sweep(repo)
    return [FileInfo.of(row) for row in repo.list(claims["sub"], limit=limit, offset=offset)]


@router.get("/{file_id}", response_model=FileInfo)
def get_file(claims: Claims, repo: FileRepo, file_id: uuid.UUID) -> FileInfo:
    row = repo.get(claims["sub"], file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")
    return FileInfo.of(row)
```

**Also delete the old `download_file` and `delete_file` routes in this step.** They are declared `@router.get("/{name}")` and `@router.delete("/{name}")`, which collide with the new `@router.get("/{file_id}")` — FastAPI matches whichever was registered first, so leaving them would silently shadow the new route and fail this task's tests. Keep `_key` for now: the old `upload_file` still calls it, and Task 6 removes both together.

Two things go red from this commit and stay red until later tasks fix them, deliberately:
- `backend/tests/integration/test_files_minio.py` — all 7 tests drive the old name-addressed API. Task 10 rewrites it, once the routes have stopped moving.
- `backend/tests/regression/test_openapi_contract.py` — the API shape genuinely changed. Task 13 regenerates the snapshot, last, so one reviewed diff covers every change in this plan.

Download and delete return 404 between this task and Tasks 8–9. That is expected mid-plan; each task's own tests pass.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
make format && make lint-backend
git add backend/app/api/routes/files.py backend/tests/unit/test_files.py
git commit -m "feat(files): serve the file list and detail from Postgres, addressed by id

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Upload, with its guards

**Files:**
- Modify: `backend/app/api/routes/files.py` (replace `upload_file`, remove `_key` and `_prefix`)
- Modify: `backend/tests/unit/test_files.py` (add the upload tests)
- Modify: `backend/tests/regression/test_pr12_unsafe_file_names.py`
- Modify: `backend/tests/conftest.py` (`FakeMinio.put_object` must return a version id)

**Interfaces:**
- Consumes: `FileRepository.reserve`, `.finalize`, `.abandon`, `QuotaExceeded` (Task 4); `FileInfo` (Task 5).
- Produces: `POST /api/files` returning `201` with a `FileInfo` body. Helpers `_validated_name(raw: str) -> str` and `_measure(stream) -> int` in the same module.

- [ ] **Step 1: Teach the MinIO fake to return a version id**

Versioning is enabled on the bucket, so the real `put_object` returns an `ObjectWriteResult` carrying `version_id`. Replace `FakeMinio.put_object` in `backend/tests/conftest.py`:

```python
    def put_object(self, _bucket, key, data, length, content_type, part_size=None):
        self.objects[key] = (data.read(), content_type)
        # The real client returns an ObjectWriteResult; only version_id is read.
        return SimpleNamespace(version_id=f"v-{len(self.objects)}")
```

- [ ] **Step 2: Write the failing upload tests**

Append to `backend/tests/unit/test_files.py`:

```python
def upload(name="a.txt", data=b"hello", sub="user-1", content_type="text/plain"):
    return client.post(
        "/api/files", headers=auth(sub), files={"file": (name, data, content_type)}
    )


def test_upload_stores_the_bytes_under_a_uuid_key(repo, store):
    response = upload("bail été.txt")

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "bail été.txt"
    assert store.objects[f"user-1/{body['id']}"] == (b"hello", "text/plain")


def test_upload_makes_the_file_listable(repo, store):
    created = upload().json()

    assert [f["id"] for f in client.get("/api/files", headers=auth()).json()] == [created["id"]]


def test_upload_writes_an_audit_row(repo, store):
    created = upload().json()

    actor, action, target_type, target_id, detail = repo.audits[-1]
    assert (actor, action, target_type) == ("user-1", "upload", "file")
    assert str(target_id) == created["id"]
    assert detail == {"name": "a.txt", "size": 5}


def test_upload_refuses_a_file_over_the_size_limit(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 4, raising=False)

    response = upload(data=b"too long")

    assert response.status_code == 413
    assert store.objects == {}
    assert repo.rows == []


def test_upload_refuses_a_file_over_the_quota(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "user_quota_bytes", 6, raising=False)
    upload(name="first.txt")

    response = upload(name="second.txt")

    assert response.status_code == 413
    assert "bytes used" in response.json()["detail"]


def test_upload_refuses_a_denied_extension(repo, store):
    response = upload(name="payload.svg", content_type="image/svg+xml")

    assert response.status_code == 415
    assert store.objects == {}


def test_a_failed_upload_leaves_no_reservation(repo, store, monkeypatch):
    def explode(*_args, **_kwargs):
        raise S3Error(None, "InternalError", "boom", "k", "", "")

    monkeypatch.setattr(store, "put_object", explode)

    response = upload()

    assert response.status_code == 502
    assert repo.rows == []
    assert client.get("/api/files", headers=auth()).json() == []


def test_upload_needs_a_token(repo):
    assert client.post("/api/files", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 401
```

Add the two imports these need at the top of the file:

```python
from minio.error import S3Error

from app.core.config import get_settings
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v -k upload`
Expected: FAIL — the route still returns 204 and writes `user-1/a.txt`.

- [ ] **Step 4: Replace the upload route**

In `backend/app/api/routes/files.py`, delete `_prefix` and `_key`, and replace `upload_file`:

```python
def _validated_name(raw: str) -> str:
    """Validate a *display* name.

    The object key is a UUID now, so a slash in the name can no longer escape
    the owner's prefix -- traversal is structurally impossible rather than
    merely filtered. What is left are display rules: something non-empty, no
    control characters, and short enough to show in a table.
    """
    name = raw.strip()
    if not name or name in (".", "..") or len(name) > 255:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid file name")
    if any(character < " " or character == "\x7f" for character in name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid file name")
    return name


def _measure(stream) -> int:
    """Exact size of an already-buffered upload. Starlette has read the whole
    body before the handler runs, so this is authoritative -- the
    Content-Length check below is only there to reject the obvious early."""
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


@router.post("", status_code=status.HTTP_201_CREATED, response_model=FileInfo)
def upload_file(claims: Claims, repo: FileRepo, file: UploadFile) -> FileInfo:
    settings = get_settings()
    name = _validated_name(file.filename or "")

    if any(name.lower().endswith(suffix) for suffix in settings.denied_extensions):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Files of this type are not accepted: {name}"
        )

    size = _measure(file.file)
    if size > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File is {size} bytes; the limit is {settings.max_upload_bytes}",
        )

    content_type = file.content_type or "application/octet-stream"
    try:
        row = repo.reserve(
            claims["sub"],
            name=name,
            folder_id=None,
            size_bytes=size,
            content_type=content_type,
            quota_bytes=settings.user_quota_bytes,
        )
    except QuotaExceeded as e:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Quota exceeded: {e.used} of {e.limit} bytes used, {e.needed} more needed",
        ) from e

    try:
        written = minio_client().put_object(
            settings.s3_bucket, row.object_key, file.file, length=size, content_type=content_type
        )
    except S3Error as e:
        # The reservation is released immediately rather than waiting for the
        # sweeper, so a storage outage does not eat anyone's quota for an hour.
        repo.abandon(row)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Storage is unavailable") from e

    repo.finalize(row, s3_version_id=written.version_id or "", actor_sub=claims["sub"])
    repo.audit(claims["sub"], "upload", "file", row.id, {"name": name, "size": size})
    return FileInfo.of(row)
```

Add `QuotaExceeded` to the repository import at the top of the module:

```python
from app.repositories.files import FileRepo, QuotaExceeded
```

- [ ] **Step 5: Run the upload tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v`
Expected: all pass (9 read tests + 8 upload tests).

- [ ] **Step 6: Re-pin the PR #12 regression to the new invariant**

The old test pinned "a name with a slash is rejected, because it would escape the key prefix". That defence is gone — and so is the vulnerability, because the name is no longer part of the key. Replace `backend/tests/regression/test_pr12_unsafe_file_names.py` so it pins the *stronger* property:

```python
"""Regression — PR #12, unsafe file names.

Originally: `_key()` concatenated the caller's `sub` with the upload's
filename, so `..` or a slash in that filename could escape the owner's prefix,
and had to be rejected before anything reached MinIO.

Since the metadata moved to Postgres the object key is `<sub>/<uuid>` and the
filename is only ever a display string, so traversal is structurally
impossible rather than filtered. This test now pins *that* -- a hostile name
is stored verbatim as a label while the key stays a UUID under the right
owner -- plus the display rules that survived.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


def post(name):
    return client.post(
        "/api/files",
        headers={"Authorization": f"Bearer {token()}"},
        files={"file": (name, b"hello", "text/plain")},
    )


@pytest.mark.parametrize("name", ["../../etc/passwd", "a/b", "a\\b", "..\\..\\x"])
def test_a_hostile_name_cannot_shape_the_object_key(name, repo, store):
    response = post(name)

    assert response.status_code == 201
    assert response.json()["name"] == name

    key = next(iter(store.objects))
    owner, _, suffix = key.partition("/")
    assert owner == "user-1"
    uuid.UUID(suffix)  # raises if the key is anything but a plain UUID


@pytest.mark.parametrize("name", ["", "   ", ".", "..", "x" * 256, "bad\x00name"])
def test_undisplayable_names_are_still_rejected(name, repo, store):
    assert post(name).status_code == 422
    assert store.objects == {}
```

- [ ] **Step 7: Run the regression suite**

Run: `make test-regression`
Expected: the PR12 tests pass. `test_openapi_contract` still FAILS — expected since Task 5, and Task 13 regenerates the snapshot deliberately last, so one reviewed diff covers every change in this plan.

- [ ] **Step 8: Commit**

```bash
make format && make lint-backend
git add backend/app/api/routes/files.py backend/tests/unit/test_files.py backend/tests/conftest.py backend/tests/regression/test_pr12_unsafe_file_names.py
git commit -m "feat(files): upload through Postgres with size, type and quota guards

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: A same-name upload appends a version

**Files:**
- Modify: `backend/app/api/routes/files.py` (`upload_file`)
- Modify: `backend/tests/unit/test_files.py`
- Test: `backend/tests/integration/test_files_minio.py` (add a real-MinIO version check)

**Interfaces:**
- Consumes: `FileRepository.find_by_name`, `.add_version`, `.used_bytes` (Task 4).
- Produces: no new public surface — `POST /api/files` now returns `200` with the existing file's id when the name collides, instead of `201`.

**Why:** spec BR-2. Today a same-name upload overwrites, and Phase 1 must not change what a user sees. With versioning, "overwrite" is exactly "append a version" — and it is also what makes the `uq_files_folder_name` unique index satisfiable.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_files.py`:

```python
def test_a_same_name_upload_versions_the_existing_file(repo, store):
    first = upload(name="notes.txt", data=b"one").json()

    second = client.post(
        "/api/files",
        headers=auth(),
        files={"file": ("notes.txt", b"two now", "text/plain")},
    )

    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
    assert second.json()["size"] == 7
    assert repo.versions[uuid.UUID(first["id"])] == 2
    assert len(repo.rows) == 1


def test_the_new_version_replaces_the_bytes_at_the_same_key(repo, store):
    created = upload(name="notes.txt", data=b"one").json()

    client.post(
        "/api/files", headers=auth(), files={"file": ("notes.txt", b"two", "text/plain")}
    )

    assert store.objects[f"user-1/{created['id']}"] == (b"two", "text/plain")


def test_a_same_name_upload_by_another_owner_is_a_new_file(repo, store):
    first = upload(name="notes.txt", sub="user-1").json()

    second = client.post(
        "/api/files", headers=auth("user-2"), files={"file": ("notes.txt", b"x", "text/plain")}
    )

    assert second.status_code == 201
    assert second.json()["id"] != first["id"]


def test_a_versioning_upload_still_respects_the_quota(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "user_quota_bytes", 10, raising=False)
    upload(name="notes.txt", data=b"abc")

    response = client.post(
        "/api/files", headers=auth(), files={"file": ("notes.txt", b"x" * 20, "text/plain")}
    )

    assert response.status_code == 413
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v -k same_name`
Expected: FAIL — the second upload returns 201 and creates a second row.

- [ ] **Step 3: Branch the upload route on a name collision**

In `upload_file`, insert the collision branch **after the `content_type = file.content_type or ...` line and before the `try:` that wraps `repo.reserve`** — putting it any earlier references `content_type` before it is assigned:

```python
    existing = repo.find_by_name(claims["sub"], name, None)
    if existing is not None:
        return _append_version(claims, repo, existing, file, size, content_type)
```

and add the helper below `upload_file`:

```python
def _append_version(
    claims: Claims, repo: FileRepo, existing: File, file: UploadFile, size: int, content_type: str
) -> FileInfo:
    """BR-2: a same-name upload into the same folder is a new version of that
    file, not a second file. This is what today's overwrite becomes once the
    bucket's versions are indexed."""
    settings = get_settings()

    # The delta, because the old version's bytes are about to stop counting.
    projected = repo.used_bytes(claims["sub"]) - existing.size_bytes + size
    if projected > settings.user_quota_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Quota exceeded: {projected} bytes needed, the limit is {settings.user_quota_bytes}",
        )

    try:
        written = minio_client().put_object(
            settings.s3_bucket,
            existing.object_key,
            file.file,
            length=size,
            content_type=content_type,
        )
    except S3Error as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Storage is unavailable") from e

    repo.add_version(
        existing,
        s3_version_id=written.version_id or "",
        size_bytes=size,
        content_type=content_type,
        actor_sub=claims["sub"],
    )
    repo.audit(claims["sub"], "upload", "file", existing.id, {"name": existing.name, "size": size})
    return FileInfo.of(existing)
```

Note this path is not covered by a `pending` reservation: the row already exists and is `ready`, so the quota check above is advisory and a concurrent pair of version uploads could briefly exceed the quota by one delta.
<!-- ponytail: version uploads check quota without the advisory lock; add reserve_version if overshoot ever matters -->

The route's declared `status_code=201` is wrong for this branch. Change the decorator and set the code explicitly:

```python
@router.post("", response_model=FileInfo, responses={201: {"model": FileInfo}})
def upload_file(claims: Claims, repo: FileRepo, file: UploadFile, response: Response) -> FileInfo:
```

and in the new-file path, just before `return FileInfo.of(row)`:

```python
    response.status_code = status.HTTP_201_CREATED
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v`
Expected: all pass (21 tests).

The real-MinIO proof that two puts leave two retrievable versions lands in Task 10, once the integration file has been rewritten around the new API.

- [ ] **Step 5: Commit**

```bash
make format && make lint-backend
git add backend/app/api/routes/files.py backend/tests/unit/test_files.py
git commit -m "feat(files): a same-name upload appends a version instead of a second file

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Serve content, with the inline allow-list

**Files:**
- Modify: `backend/app/api/routes/files.py` (replace `download_file`)
- Modify: `backend/tests/unit/test_files.py`

**Interfaces:**
- Consumes: `FileRepository.get` (Task 4).
- Produces: `GET /api/files/{file_id}/content?disposition=attachment|inline`.

**Why the allow-list is the security boundary:** the SPA and the API share the origin `darkangel.infra.famillelallier.net`. An uploaded `.html` or `.svg` served inline would run JavaScript in that origin and could read the SPA's tokens. Nothing outside the allow-list is ever served inline, whatever it claims to be, and nothing outside it is served with its own content type either.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_files.py`:

```python
def test_content_streams_the_bytes_as_an_attachment(repo, store):
    created = upload(name="bail été.txt", data=b"hello").json()

    response = client.get(f"/api/files/{created['id']}/content", headers=auth())

    assert response.status_code == 200
    assert response.content == b"hello"
    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''bail%20%C3%A9t%C3%A9.txt"
    )


def test_a_name_with_a_slash_is_fully_escaped_in_the_header(repo, store):
    # quote() leaves '/' alone by default, which would split the header value.
    created = upload(name="a/b.txt").json()

    response = client.get(f"/api/files/{created['id']}/content", headers=auth())

    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''a%2Fb.txt"


def test_an_allow_listed_type_may_be_served_inline(repo, store):
    created = upload(name="shot.png", data=b"\x89PNG", content_type="image/png").json()

    response = client.get(
        f"/api/files/{created['id']}/content?disposition=inline", headers=auth()
    )

    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["content-type"].startswith("image/png")


def test_anything_else_is_forced_to_attachment(repo, store):
    created = upload(
        name="sheet.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ).json()

    response = client.get(
        f"/api/files/{created['id']}/content?disposition=inline", headers=auth()
    )

    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["content-type"].startswith("application/octet-stream")


def test_every_content_response_carries_the_hardening_headers(repo, store):
    created = upload().json()

    response = client.get(f"/api/files/{created['id']}/content", headers=auth())

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"


def test_content_hides_another_owners_file_behind_404(repo, store):
    created = upload().json()

    assert (
        client.get(f"/api/files/{created['id']}/content", headers=auth("user-2")).status_code
        == 404
    )


def test_content_needs_a_token(repo, store):
    created = upload().json()

    assert client.get(f"/api/files/{created['id']}/content").status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v -k content`
Expected: FAIL — 404, the `/content` route does not exist.

- [ ] **Step 3: Replace the download route**

In `backend/app/api/routes/files.py`, replace `download_file` entirely:

```python
@router.get("/{file_id}/content")
def download_file(
    claims: Claims,
    repo: FileRepo,
    file_id: uuid.UUID,
    disposition: Literal["attachment", "inline"] = "attachment",
) -> StreamingResponse:
    row = repo.get(claims["sub"], file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")

    settings = get_settings()
    # The SPA and the API share an origin, so anything rendered inline runs in
    # it. Only the allow-list is ever rendered, and only the allow-list keeps
    # its own content type -- everything else downloads as opaque bytes.
    renderable = row.content_type in settings.inline_content_types
    mode = "inline" if (disposition == "inline" and renderable) else "attachment"
    served_type = row.content_type if renderable else "application/octet-stream"

    try:
        obj = minio_client().get_object(settings.s3_bucket, row.object_key)
    except S3Error as e:
        if e.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file") from e
        raise

    def chunks() -> Iterator[bytes]:
        try:
            yield from obj.stream(64 * 1024)
        finally:
            obj.close()
            obj.release_conn()

    return StreamingResponse(
        chunks(),
        media_type=served_type,
        headers={
            # safe="" matters: a display name may hold a '/' now, and quote()
            # would otherwise leave it raw and split the header value.
            "Content-Disposition": f"{mode}; filename*=UTF-8''{quote(row.name, safe='')}",
            "Content-Length": obj.headers["Content-Length"],
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )
```

Add `Literal` to the typing import at the top of the module:

```python
from typing import Literal
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v`
Expected: all pass (28 tests).

- [ ] **Step 5: Commit**

```bash
make format && make lint-backend
git add backend/app/api/routes/files.py backend/tests/unit/test_files.py
git commit -m "feat(files): stream content by id behind an inline content-type allow-list

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Soft delete

**Files:**
- Modify: `backend/app/api/routes/files.py` (replace `delete_file`)
- Modify: `backend/tests/unit/test_files.py`

**Interfaces:**
- Consumes: `FileRepository.get`, `.soft_delete`, `.audit` (Task 4).
- Produces: `DELETE /api/files/{file_id}` → `204`.

**Why soft, in Phase 1:** the Trash UI is Phase 3, but the delete *semantics* have to be settled now — changing them later would be a second behaviour change for users. From the outside this is identical to today: the file disappears from the list. The bytes stay in MinIO, which is already true today anyway, because the bucket is versioned and a delete only writes a delete marker.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_files.py`:

```python
def test_delete_removes_the_file_from_the_list(repo, store):
    created = upload().json()

    assert client.delete(f"/api/files/{created['id']}", headers=auth()).status_code == 204
    assert client.get("/api/files", headers=auth()).json() == []


def test_delete_keeps_the_bytes_and_the_row(repo, store):
    created = upload().json()

    client.delete(f"/api/files/{created['id']}", headers=auth())

    assert store.objects[f"user-1/{created['id']}"] == (b"hello", "text/plain")
    assert len(repo.rows) == 1


def test_delete_writes_an_audit_row(repo, store):
    created = upload().json()

    client.delete(f"/api/files/{created['id']}", headers=auth())

    actor, action, _, target_id, detail = repo.audits[-1]
    assert (actor, action) == ("user-1", "delete")
    assert str(target_id) == created["id"]
    assert detail == {"name": "a.txt"}


def test_delete_cannot_reach_another_owners_file(repo, store):
    created = upload().json()

    assert client.delete(f"/api/files/{created['id']}", headers=auth("user-2")).status_code == 404
    assert len(client.get("/api/files", headers=auth()).json()) == 1


def test_deleting_twice_is_a_404(repo, store):
    created = upload().json()
    client.delete(f"/api/files/{created['id']}", headers=auth())

    assert client.delete(f"/api/files/{created['id']}", headers=auth()).status_code == 404


def test_the_name_is_free_again_after_a_delete(repo, store):
    first = upload(name="notes.txt").json()
    client.delete(f"/api/files/{first['id']}", headers=auth())

    second = upload(name="notes.txt")

    assert second.status_code == 201
    assert second.json()["id"] != first["id"]


def test_delete_needs_a_token(repo, store):
    created = upload().json()

    assert client.delete(f"/api/files/{created['id']}").status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v -k delete`
Expected: FAIL — the route still takes a name and calls `remove_object`.

- [ ] **Step 3: Replace the delete route**

```python
@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(claims: Claims, repo: FileRepo, file_id: uuid.UUID) -> Response:
    """Soft: the row is marked, the bytes stay. From the outside this is what
    today's delete already looks like -- the bucket is versioned, so even the
    old hard delete only ever wrote a delete marker. The Trash that makes the
    difference visible is Phase 3."""
    row = repo.get(claims["sub"], file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")

    repo.soft_delete(row)
    repo.audit(claims["sub"], "delete", "file", row.id, {"name": row.name})
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_files.py -v`
Expected: all pass (35 tests).

- [ ] **Step 5: Run every backend suite and confirm only the known failures remain**

Run: `make test-unit && make test-regression && make test-integration`
Expected: unit green (35). Exactly two known failures remain, both scheduled: `test_openapi_matches_the_snapshot` in regression (Task 13) and the 7 old API tests in `tests/integration/test_files_minio.py` (Task 10). Any *other* failure is a regression introduced by Tasks 5–9 — stop and fix it before continuing.

- [ ] **Step 6: Commit**

```bash
make format && make lint-backend
git add backend/app/api/routes/files.py backend/tests/unit/test_files.py
git commit -m "feat(files): delete by id, soft, with an audit row

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Rewrite the real-MinIO integration tests

**Files:**
- Modify: `backend/tests/integration/test_files_minio.py` (all 7 tests)
- Modify: `backend/tests/integration/conftest.py` (enable versioning on the throwaway bucket)

**Interfaces:**
- Consumes: the full route surface from Tasks 5–9, the `db` fixture (Task 2), `minio_bucket` (existing).
- Produces: no code surface. This is the end-to-end proof that the routes work against a real MinIO *and* a real Postgres together — the fakes in the unit suite cannot show that.

**Why now and not earlier:** these tests drive the HTTP API, which moved under them in Tasks 5–9. Rewriting them once, after the routes settle, beats rewriting them five times.

- [ ] **Step 1: Enable versioning on the test bucket**

Production enables it (`scripts/provision-minio.sh:51`) but `make_bucket` does not, so the throwaway bucket must match or version ids come back `None`. In `backend/tests/integration/conftest.py`, in the `minio_bucket` fixture, immediately after `client.make_bucket(settings.s3_bucket)`:

```python
        # Production enables versioning (provision-minio.sh); the throwaway
        # bucket must match, or put_object returns no version_id and the
        # file_versions rows would all record an empty string.
        client.set_bucket_versioning(settings.s3_bucket, VersioningConfig(ENABLED))
```

with the import added at the top of that file:

```python
from minio.versioningconfig import ENABLED, VersioningConfig
```

- [ ] **Step 2: Rewrite the test module**

Replace `backend/tests/integration/test_files_minio.py` entirely:

```python
"""Integration — the file routes against a real MinIO and a real PostgreSQL.

The unit suite proves the routes' logic against fakes. This one proves the two
real services agree: that the row the API returns points at bytes that are
actually there, under a key the owner owns, with a version id MinIO issued.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.files import File, FileVersion
from tests.conftest import token

client = TestClient(app)

# put_object is called with the exact measured length now, so a large upload
# still exercises the SDK's multipart path.
PART_SIZE = 10 * 1024 * 1024


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def upload(name, data=b"hello", sub="user-1", content_type="text/plain"):
    return client.post("/api/files", headers=auth(sub), files={"file": (name, data, content_type)})


def test_upload_list_download_delete_round_trip(db, minio_bucket):
    created = upload("notes.txt", b"hello")
    assert created.status_code == 201
    file_id = created.json()["id"]

    listed = client.get("/api/files", headers=auth()).json()
    assert [f["name"] for f in listed] == ["notes.txt"]
    assert listed[0]["size"] == 5
    # Real Postgres stamps updated_at; nothing here can be None.
    assert listed[0]["modified"] is not None

    got = client.get(f"/api/files/{file_id}/content", headers=auth())
    assert got.status_code == 200
    assert got.content == b"hello"

    assert client.delete(f"/api/files/{file_id}", headers=auth()).status_code == 204
    assert client.get("/api/files", headers=auth()).json() == []


def test_the_object_key_is_a_uuid_under_the_owner(db, minio_bucket):
    file_id = upload("notes.txt").json()["id"]

    row = db.query(File).one()
    assert row.object_key == f"user-1/{file_id}"
    uuid.UUID(row.object_key.split("/")[1])


def test_minio_issues_a_real_version_id(db, minio_bucket):
    upload("notes.txt")

    version = db.query(FileVersion).one()
    assert version.version_no == 1
    assert version.s3_version_id  # not the empty-string fallback


def test_a_second_upload_of_one_name_leaves_two_versions(db, minio_bucket):
    first = upload("notes.txt", b"one").json()
    second = upload("notes.txt", b"two and a bit").json()

    assert second["id"] == first["id"]
    assert db.query(File).count() == 1

    versions = db.query(FileVersion).order_by(FileVersion.version_no).all()
    assert [v.version_no for v in versions] == [1, 2]
    assert versions[0].s3_version_id != versions[1].s3_version_id

    got = client.get(f"/api/files/{first['id']}/content", headers=auth())
    assert got.content == b"two and a bit"


def test_users_cannot_reach_each_others_files(db, minio_bucket):
    file_id = upload("secret.txt", sub="user-1").json()["id"]

    assert client.get("/api/files", headers=auth("user-2")).json() == []
    assert client.get(f"/api/files/{file_id}", headers=auth("user-2")).status_code == 404
    assert client.get(f"/api/files/{file_id}/content", headers=auth("user-2")).status_code == 404
    assert client.delete(f"/api/files/{file_id}", headers=auth("user-2")).status_code == 404


@pytest.mark.parametrize("name", ["bail été.txt", "a b c.txt", "naïve ✓.txt", "a/b.txt"])
def test_names_with_unicode_spaces_and_slashes_round_trip(name, db, minio_bucket):
    # The name is display data now, so even a slash is stored verbatim.
    file_id = upload(name).json()["id"]

    assert client.get(f"/api/files/{file_id}", headers=auth()).json()["name"] == name


def test_a_multipart_sized_upload_survives_the_round_trip(db, minio_bucket):
    payload = b"x" * (PART_SIZE + 1024)

    file_id = upload("big.bin", payload, content_type="application/octet-stream").json()["id"]

    got = client.get(f"/api/files/{file_id}/content", headers=auth())
    assert got.content == payload
    assert db.query(File).one().size_bytes == len(payload)


def test_downloading_a_missing_file_is_404(db, minio_bucket):
    assert client.get(f"/api/files/{uuid.uuid4()}/content", headers=auth()).status_code == 404


def test_deleting_a_missing_file_is_404(db, minio_bucket):
    # Changed from the old behaviour: remove_object on a missing key was a
    # silent success, but a soft delete needs a row to mark.
    assert client.delete(f"/api/files/{uuid.uuid4()}", headers=auth()).status_code == 404


def test_the_bytes_outlive_a_soft_delete(db, minio_bucket):
    from app.api.routes.files import minio_client

    file_id = upload("notes.txt").json()["id"]
    key = db.query(File).one().object_key

    client.delete(f"/api/files/{file_id}", headers=auth())

    # The row is marked, not removed, and MinIO still holds the object -- which
    # is what makes Phase 3's undelete possible.
    assert db.query(File).one().deleted_at is not None
    assert minio_client().stat_object(minio_bucket, key).size == 5


def test_a_quota_refusal_leaves_nothing_behind(db, minio_bucket, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "user_quota_bytes", 3)

    assert upload("too-big.txt", b"hello").status_code == 413
    assert db.query(File).count() == 0
    from app.api.routes.files import minio_client

    assert list(minio_client().list_objects(minio_bucket, recursive=True)) == []
```

- [ ] **Step 3: Run the integration suite**

Run: `make services-test-up && make test-integration`
Expected: every integration test passes — `test_schema.py` (10), `test_repository.py` (14), `test_files_minio.py` (14 with the parametrised cases).

- [ ] **Step 4: Commit**

```bash
make format && make lint-backend
git add backend/tests/integration/test_files_minio.py backend/tests/integration/conftest.py
git commit -m "test(files): drive the id-addressed API against real MinIO and Postgres

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Backfill the objects that are already in the bucket

**Files:**
- Create: `backend/app/scripts/__init__.py`, `backend/app/scripts/backfill.py`
- Modify: `Makefile`
- Test: `backend/tests/integration/test_backfill.py`

**Interfaces:**
- Consumes: `app.core.db.session_factory`, `app.models.files.{File, FileVersion}`, `app.api.routes.files.minio_client`.
- Produces: `app.scripts.backfill.backfill(dry_run: bool = False) -> list[tuple[str, str]]` returning `(old_key, new_key)` pairs, and a `__main__` entry point taking `--dry-run`.

**Danger:** this rewrites live data. Step 1 is not optional.

- [ ] **Step 1: Write down the mirror command in the module docstring**

The operator must mirror the bucket before the first real run. The script refuses to run without an explicit `--confirm` so nobody does it by reflex.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/integration/test_backfill.py`:

```python
"""Integration — the one-shot migration from <sub>/<name> keys to <sub>/<uuid>."""

import io
import uuid

from app.api.routes.files import minio_client
from app.models.files import File, FileVersion
from app.scripts.backfill import backfill


def put(bucket, key, data=b"hello"):
    minio_client().put_object(bucket, key, io.BytesIO(data), length=len(data))


def test_a_legacy_object_gets_a_row_and_a_uuid_key(db, minio_bucket):
    put(minio_bucket, "user-1/bail été.txt")

    moved = backfill(confirm=True)

    assert len(moved) == 1
    old_key, new_key = moved[0]
    assert old_key == "user-1/bail été.txt"

    row = db.query(File).one()
    assert row.owner_sub == "user-1"
    assert row.name == "bail été.txt"
    assert row.status == "ready"
    assert row.object_key == new_key == f"user-1/{row.id}"
    assert db.query(FileVersion).one().version_no == 1


def test_the_bytes_survive_the_move(db, minio_bucket):
    put(minio_bucket, "user-1/a.txt", b"the original")

    backfill(confirm=True)

    row = db.query(File).one()
    got = minio_client().get_object(minio_bucket, row.object_key)
    assert got.read() == b"the original"


def test_the_old_key_is_gone(db, minio_bucket):
    put(minio_bucket, "user-1/a.txt")

    backfill(confirm=True)

    keys = {o.object_name for o in minio_client().list_objects(minio_bucket, recursive=True)}
    assert "user-1/a.txt" not in keys


def test_a_second_run_changes_nothing(db, minio_bucket):
    put(minio_bucket, "user-1/a.txt")
    backfill(confirm=True)

    assert backfill(confirm=True) == []
    assert db.query(File).count() == 1


def test_dry_run_writes_nothing(db, minio_bucket):
    put(minio_bucket, "user-1/a.txt")

    moved = backfill(confirm=True, dry_run=True)

    assert len(moved) == 1
    assert db.query(File).count() == 0
    keys = {o.object_name for o in minio_client().list_objects(minio_bucket, recursive=True)}
    assert keys == {"user-1/a.txt"}


def test_it_refuses_to_run_without_confirmation(db, minio_bucket):
    put(minio_bucket, "user-1/a.txt")

    try:
        backfill()
    except RuntimeError as e:
        assert "mirror" in str(e).lower()
    else:
        raise AssertionError("backfill() must refuse to rewrite live data unconfirmed")


def test_each_owner_keeps_their_own_files(db, minio_bucket):
    put(minio_bucket, "user-1/a.txt")
    put(minio_bucket, "user-2/a.txt")

    backfill(confirm=True)

    owners = {row.owner_sub: row.object_key for row in db.query(File).all()}
    assert set(owners) == {"user-1", "user-2"}
    assert all(key.startswith(f"{owner}/") for owner, key in owners.items())
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.scripts'`

- [ ] **Step 4: Write the script**

Create `backend/app/scripts/__init__.py` (empty), and `backend/app/scripts/backfill.py`:

```python
"""One-shot: move legacy `<sub>/<name>` objects to `<sub>/<uuid>` and index them.

    make backfill ARGS=--dry-run     # list what would move, change nothing
    make backfill ARGS=--confirm     # do it

MIRROR THE BUCKET FIRST. This rewrites live data, and the server-side copy
starts a fresh version chain: MinIO versions predating the move do not follow
the object to its new key.

    mc mirror --preserve infra/darkangel-files ./darkangel-files-backup

Idempotent: a key already recorded in files.object_key is skipped, so a run
interrupted halfway can simply be repeated.
"""

import argparse
import uuid

from minio.commonconfig import CopySource
from sqlalchemy import select

from app.api.routes.files import minio_client
from app.core.config import get_settings
from app.core.db import session_factory
from app.models.files import File, FileVersion

MIRROR_WARNING = (
    "backfill rewrites live objects. Mirror the bucket first "
    "(`mc mirror --preserve infra/darkangel-files ./backup`), then pass confirm=True."
)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def backfill(*, confirm: bool = False, dry_run: bool = False) -> list[tuple[str, str]]:
    if not confirm:
        raise RuntimeError(MIRROR_WARNING)

    settings = get_settings()
    client = minio_client()
    moved: list[tuple[str, str]] = []

    with session_factory()() as session:
        known = set(session.scalars(select(File.object_key)).all())

        for obj in client.list_objects(settings.s3_bucket, recursive=True):
            old_key = obj.object_name
            owner_sub, _, name = old_key.partition("/")
            if not name or _is_uuid(name) or old_key in known:
                continue

            file_id = uuid.uuid4()
            new_key = f"{owner_sub}/{file_id}"
            moved.append((old_key, new_key))
            if dry_run:
                continue

            written = client.copy_object(
                settings.s3_bucket, new_key, CopySource(settings.s3_bucket, old_key)
            )
            stat = client.stat_object(settings.s3_bucket, new_key)
            row = File(
                id=file_id,
                owner_sub=owner_sub,
                folder_id=None,
                name=name,
                content_type=stat.content_type or "application/octet-stream",
                size_bytes=stat.size,
                object_key=new_key,
                status="ready",
            )
            version = FileVersion(
                id=uuid.uuid4(),
                file_id=file_id,
                version_no=1,
                s3_version_id=written.version_id or "",
                size_bytes=stat.size,
                content_type=row.content_type,
                created_by=owner_sub,
            )
            row.current_version_id = version.id
            session.add(row)
            session.add(version)
            session.commit()

            # Only after the row is committed: a crash here leaves a duplicate
            # object, which the next run skips, rather than a lost file.
            client.remove_object(settings.s3_bucket, old_key)

    return moved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="list the moves, change nothing")
    parser.add_argument("--confirm", action="store_true", help="acknowledge the mirror warning")
    args = parser.parse_args()

    moved = backfill(confirm=args.confirm or args.dry_run, dry_run=args.dry_run)
    for old_key, new_key in moved:
        print(f"{'would move' if args.dry_run else 'moved'} {old_key} -> {new_key}")
    print(f"{len(moved)} object(s)")


if __name__ == "__main__":
    main()
```

Add the Makefile target:

```make
backfill: $(PY) ## Move legacy <sub>/<name> objects to <sub>/<uuid>; ARGS=--dry-run first
	cd $(BACKEND) && .venv/bin/python -m app.scripts.backfill $(ARGS)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_backfill.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
make format && make lint-backend
git add backend/app/scripts backend/tests/integration/test_backfill.py Makefile
git commit -m "feat(files): add the legacy-key backfill, idempotent and dry-runnable

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Point the SPA at the id-addressed API

**Files:**
- Modify: `frontend/src/api/files.ts`, `frontend/src/stores/files.ts`, `frontend/src/views/FilesView.vue`
- Test: `frontend/tests/unit/stores-files.test.ts`, `frontend/tests/unit/FilesView.test.ts`, `frontend/tests/regression/pr12-download-blob-url.test.ts`

**Interfaces:**
- Consumes: `GET /api/files`, `POST /api/files`, `GET /api/files/{id}/content`, `DELETE /api/files/{id}`.
- Produces: `HomeFile { id: string; name: string; size: number; content_type: string; modified: string | null }`; `listFiles(): Promise<HomeFile[]>`; `uploadFile(file: File): Promise<HomeFile>`; `downloadFile(id: string): Promise<Blob>`; `deleteFile(id: string): Promise<void>`; store action `remove(id: string)`.

**Visible change: none.** The table looks and behaves exactly as before. This task only changes what the SPA sends over the wire.

- [ ] **Step 1: Write the failing store test**

Replace the delete-related test in `frontend/tests/unit/stores-files.test.ts` (read the file first and keep its existing structure and mocking style) and add:

```ts
it('deletes by id, not by name', async () => {
  const store = useFilesStore()
  await store.remove('11111111-1111-1111-1111-111111111111')

  expect(deleteFile).toHaveBeenCalledWith('11111111-1111-1111-1111-111111111111')
})
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm run test -- stores-files`
Expected: FAIL — a type error or a call with the wrong argument.

- [ ] **Step 3: Rewrite the API module**

Replace `frontend/src/api/files.ts`:

```ts
import { apiGet, apiRequest } from './client'

export interface HomeFile {
  id: string
  name: string
  size: number
  content_type: string
  modified: string | null
}

export function listFiles(): Promise<HomeFile[]> {
  return apiGet<HomeFile[]>('/files')
}

export async function uploadFile(file: File): Promise<HomeFile> {
  const form = new FormData()
  form.append('file', file)
  return (await apiRequest('POST', '/files', form)).json()
}

export async function downloadFile(id: string): Promise<Blob> {
  return (await apiRequest('GET', `/files/${id}/content`)).blob()
}

export async function deleteFile(id: string): Promise<void> {
  await apiRequest('DELETE', `/files/${id}`)
}
```

The old `filePath` helper with `encodeURIComponent` is gone: a UUID needs no escaping, and the display name never reaches the URL any more.

- [ ] **Step 4: Update the store**

In `frontend/src/stores/files.ts`, change one line:

```ts
  const remove = (id: string) => run(() => deleteFile(id))
```

- [ ] **Step 5: Update the view**

In `frontend/src/views/FilesView.vue`:

```ts
// The API wants a bearer token, so a plain <a href> cannot fetch the file:
// fetch it, then hand the blob to a throwaway link.
async function download(file: HomeFile) {
  try {
    const url = URL.createObjectURL(await downloadFile(file.id))
    const link = Object.assign(document.createElement('a'), { href: url, download: file.name })
    link.click()
    setTimeout(() => URL.revokeObjectURL(url))
  } catch (e) {
    store.error = e instanceof Error ? e.message : String(e)
  }
}

function remove(file: HomeFile) {
  if (confirm(`Delete ${file.name}?`)) store.remove(file.id)
}
```

with the import updated to `import { downloadFile, type HomeFile } from '@/api/files'`, and in the template:

```html
        <tr v-for="file in store.files" :key="file.id">
          <td>{{ file.name }}</td>
          <td>{{ formatSize(file.size) }}</td>
          <td>{{ file.modified ? new Date(file.modified).toLocaleString() : '' }}</td>
          <td class="actions">
            <button type="button" @click="download(file)">Download</button>
            <button type="button" @click="remove(file)">Delete</button>
          </td>
        </tr>
```

- [ ] **Step 6: Update the component and regression tests**

Read `frontend/tests/unit/FilesView.test.ts` and `frontend/tests/regression/pr12-download-blob-url.test.ts`, then give every fixture file an `id` and a `content_type`, and assert that download and delete are called with the id. For example, in the PR12 regression test the fixture becomes:

```ts
const file: HomeFile = {
  id: '11111111-1111-1111-1111-111111111111',
  name: 'bail été.txt',
  size: 5,
  content_type: 'text/plain',
  modified: null,
}
```

and its assertion becomes `expect(downloadFile).toHaveBeenCalledWith(file.id)`. The property that test pins — that a blob URL is created and revoked rather than navigating to a bearer-less href — is unchanged and must keep passing.

- [ ] **Step 7: Run the frontend suite and the type-checker**

Run: `make test-frontend && make typecheck`
Expected: all tests pass; `vue-tsc` reports no errors.

- [ ] **Step 8: Commit**

```bash
make format && make lint-frontend
git add frontend/src/api/files.ts frontend/src/stores/files.ts frontend/src/views/FilesView.vue frontend/tests
git commit -m "feat(spa): address files by id and download from /content

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: CI, the OpenAPI snapshot, the image, and the docs

**Files:**
- Modify: `.github/workflows/ci.yml` (the `backend-integration` job)
- Modify: `backend/tests/regression/openapi.snapshot.json` (regenerated)
- Modify: `docs/testing.md`
- Modify: `deploy/portainer-stack.yml`, `.portainer.env.example`

**Interfaces:**
- Consumes: everything above.
- Produces: no code surface. A green CI run and a reviewable snapshot diff.

- [ ] **Step 1: Add Postgres to the integration job**

In `.github/workflows/ci.yml`, rename the job and add the service. Under `backend-integration:`, change the `name:` and extend `env:`:

```yaml
  backend-integration:
    name: Backend integration (real MinIO + Postgres)
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    services:
      # A service container works here, unlike MinIO: the postgres image needs
      # no custom command, so Actions can start it directly.
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: darkangel
          POSTGRES_PASSWORD: darkangel
          POSTGRES_DB: darkangel
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U darkangel -d darkangel"
          --health-interval 2s
          --health-timeout 3s
          --health-retries 20
    env:
      # `CI` makes an unreachable MinIO or Postgres fail the job, not skip it.
      CI: "true"
      DARKANGEL_S3_ENDPOINT: localhost:9000
      DARKANGEL_S3_ACCESS_KEY: minioadmin
      DARKANGEL_S3_SECRET_KEY: minioadmin
      DARKANGEL_S3_SECURE: "false"
      DARKANGEL_DATABASE_URL: postgresql+psycopg://darkangel:darkangel@localhost:5432/darkangel
```

and update the final Summary step's text:

```yaml
      - name: Summary
        if: always()
        run: |
          echo "### Backend integration" >> "$GITHUB_STEP_SUMMARY"
          echo "Ran against MinIO at $DARKANGEL_S3_ENDPOINT and Postgres at localhost:5432." \
            >> "$GITHUB_STEP_SUMMARY"
```

No migration step is needed: the `pg_database` fixture runs `alembic upgrade head` itself.

- [ ] **Step 2: Add the database URL to the deployed stack**

In `deploy/portainer-stack.yml`, add to the `darkangel-api` service's `environment:` block, matching the commenting style of the MinIO entry above it:

```yaml
      # Metadata lives in the Infra PostgreSQL (`postgres:5432` on infra-net),
      # database darkangel as role darkangel -- both made by `make postgres`.
      # POSTGRES_PASSWORD is a stack variable `make up` copies from
      # .portainer.env, the same way MINIO_SECRET_KEY arrives.
      DARKANGEL_DATABASE_URL: postgresql+psycopg://darkangel:${POSTGRES_PASSWORD:-}@postgres:5432/darkangel
```

Add the variable to the header comment's stack-variable list, and add `POSTGRES_PASSWORD=` to `.portainer.env.example`.

- [ ] **Step 3: Ship the migrations in the image and run them on start**

`backend/Dockerfile` copies only `pyproject.toml` and `app/`, so `alembic.ini` and `migrations/` never reach the image and `alembic upgrade head` would fail with "No config file 'alembic.ini' found". Two edits.

Replace the copy block:

```dockerfile
COPY pyproject.toml ./
COPY app ./app
# Alembic is not part of the `app` package hatchling ships, so the config and
# the revision scripts are copied in explicitly; WORKDIR is /srv, which is
# where alembic.ini's `script_location = migrations` resolves from.
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install .
```

and replace the final `CMD`:

```dockerfile
# Migrate before serving. Safe at one replica, which is what the stack runs;
# spec risk R-4 records the advisory-lock fix for when that changes.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
```

- [ ] **Step 3b: Verify the image actually migrates**

Run: `docker build -t darkangel-api-test backend && docker run --rm darkangel-api-test alembic upgrade head 2>&1 | head -5`
Expected: it reaches Alembic and fails on the *connection* to `postgres:5432` (there is no database on that hostname from a bare `docker run`), not on a missing config file. `FAILED: No config file` means the COPY lines above did not take.

- [ ] **Step 4: Update the testing guide**

In `docs/testing.md`:
- Replace every `make minio-test-up` / `make minio-test-down` with `make services-test-up` / `make services-test-down`.
- Retitle the "The integration bucket" section to cover both services, and describe the `pg_database` fixture alongside `minio_bucket`: it defaults `DARKANGEL_DATABASE_URL`, migrates to head, truncates between tests, and fails rather than skips under `CI=true`.
- Add a line to "The layers" noting that route logic is unit-tested against `FakeFileRepository` while real SQL is integration-only, and why: the coverage gate measures `-m "unit or regression"`.
- Update the test counts in the guide to the real numbers from the run in Step 6.

- [ ] **Step 5: Regenerate the OpenAPI snapshot**

Run: `make snapshot`
Then: `git diff backend/tests/regression/openapi.snapshot.json`
Expected, and to be read line by line before staging: `/api/files/{name}` is gone; `/api/files/{file_id}`, `/api/files/{file_id}/content` appear; `FileInfo` gains `id` and `content_type`; `POST /api/files` gains a 201 response. Anything else in that diff is an unintended API change — stop and fix it rather than accepting the snapshot.

- [ ] **Step 6: Run the full gate**

Run: `make services-test-up && make verify && make coverage`
Expected: `format-check`, `lint`, all four suites and both builds pass; backend coverage stays at or above `COVERAGE_MIN` (80).

If coverage has dropped below the gate, that is spec risk R-5 arriving on schedule. Do not delete tests or lower the threshold to get green — report the number and the uncovered lines, and let the reviewer decide between adding unit tests against the fake repository and adjusting the gate.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/ci.yml deploy/portainer-stack.yml .portainer.env.example backend/Dockerfile backend/tests/regression/openapi.snapshot.json docs/testing.md
git commit -m "ci: run integration against Postgres; re-pin the OpenAPI snapshot

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 8: Open the pull request**

```bash
git push -u origin "$(git branch --show-current)"
gh pr create --title "Phase 1: Postgres-backed file management" --body "$(cat <<'BODY'
Implements Phase 1 of `docs/files-feature.md` — "The spine".

Metadata moves into the Infra PostgreSQL, which becomes the index; MinIO keys
become `<owner_sub>/<uuid>`. Uploads reserve a `pending` row (which is also the
quota reservation), stream to MinIO, then flip to `ready`. Adds a max file
size, a per-user quota, a denied-extension check and an inline content-type
allow-list, plus an append-only audit log enforced by a database trigger.

**Visible change: none.** The file table looks and behaves exactly as before.
That is the point of this phase.

## Deploying this

Merging is not enough — see the runbook at the end of the plan:
1. `make postgres`
2. `mc mirror --preserve infra/darkangel-files ./darkangel-files-backup`
3. `make up` (the image now runs `alembic upgrade head` on start)
4. `make backfill ARGS=--dry-run`, read it, then `make backfill ARGS=--confirm`

## Review notes

- The OpenAPI snapshot diff is intentional: `/api/files/{name}` is replaced by
  `/api/files/{file_id}` and `/api/files/{file_id}/content`.
- Ownership isolation is no longer structural — it was a MinIO key prefix, it
  is now a `WHERE owner_sub`. Every query goes through `FileRepository`, whose
  methods all take `owner_sub` as their first argument. This is risk R-1 in
  the spec and deserves the closest reading.
- `DELETE` is now soft. Externally identical today; the Trash that makes the
  difference visible is Phase 3.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
BODY
)"
```

---

## Deployment runbook (not a code task)

Phase 1 cannot be deployed by merging alone. In order, on the Infra host:

1. `make postgres` — creates the database and role, writes `POSTGRES_PASSWORD` into `.portainer.env`.
2. Mirror the bucket: `mc mirror --preserve infra/darkangel-files ./darkangel-files-backup`.
3. `make up` — deploys the new image, which runs `alembic upgrade head` on start.
4. `make backfill ARGS=--dry-run` in the API container, read the output, then `make backfill ARGS=--confirm`.
5. Check the SPA lists the same files it listed before.

Step 2 is the only thing standing between a bug in step 4 and permanent data loss.

## Out of scope for this plan

Everything in spec §11 Phases 2–4: folder routes and the folder tree UI, rename/describe/tag, search/filter/sort, version listing and restore, the Trash view and undelete, drag & drop, upload progress, and preview. The tables and columns those need are created by migration 0001, but no route or component in this plan touches them.

Two smaller deferrals worth naming, because they appear in spec §5 and could otherwise look forgotten:

- **`GET /api/files/quota`** — the guard is enforced server-side from Task 6, but nothing displays the number until the Phase 4 quota indicator, so the endpoint waits for its consumer.
- **`?folder_id=` on upload and list** — `File.folder_id` is written as `None` throughout. Wiring the parameter belongs with the folder routes in Phase 2.

The MinIO policy change in spec §10 (`s3:ListBucketVersions`, `s3:GetObjectVersion`, `s3:DeleteObjectVersion`) is **not** required by Phase 1 — nothing here reads an old version — but it must land before Phase 3 starts.
