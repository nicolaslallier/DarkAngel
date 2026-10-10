# Home dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the home page into a dashboard showing Portainer backups, Portainer instance health and the invoice status.

**Architecture:** A new `app.collector` process (second service of the stack, same backend image) polls each Portainer instance and the `portainer-backups` bucket every 5 minutes and appends rows to two PostgreSQL tables. A new `GET /api/infra` route reads the latest row per instance. `HomeView` shows three independent cards; the invoice card reuses the existing `invoices` store (`loadDashboard`).

**Tech Stack:** FastAPI, SQLAlchemy 2 + Alembic, httpx, minio client, pytest; Vue 3 + Pinia + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-10-home-dashboard-design.md`

## Global Constraints

- Backend: ruff, 100 columns; response shapes are pydantic models declared next to their route (`CLAUDE.md`).
- Frontend: `<script setup lang="ts">`, import from `@/`, no relative climbing; `fetch` only in `api/client.ts`.
- Settings are read through `get_settings()`, env prefix `DARKANGEL_`.
- Portainer API keys exist only in the collector service's environment; `darkangel-api` never gets them.
- Collector calls Portainer with read-only GETs only: `/api/system/status`, `/api/endpoints`, `/api/stacks`.
- Infra tables are not scoped by `household_id` (deliberate); `/api/infra` takes the `Reader` dependency.
- Defaults: `backup_max_age_hours` 48, `collector_interval_seconds` 300, retention 30 days, collector data is "stale" after 15 minutes.
- Instance name stored and returned = `slug(name)`: lower-case, `_` to `-` (same rule as `scripts/portainer-backup.sh`).
- Tests live in `backend/tests/{unit,integration,regression}/` and `frontend/tests/{unit,regression}/`; the directory decides the marker. Read `docs/testing.md` before adding a test.
- Commit steps: `CLAUDE.md` says to commit only when asked. Run them once the user has approved executing this plan; never commit to `main`.

## Review Focus

- Instance returns 200 with a non-JSON body, or a JSON object where a list is expected: recorded as unreachable, collector keeps going (Task 2).
- One instance down must not stop the next instance from being checked (Task 2).
- An error message must never contain the API key (Task 2).
- Empty bucket / no archive for an instance: `stale: true`, `last_backup_at: null` (Tasks 2, 3).
- Collector stopped: UI flags data older than 15 minutes (Task 4).
- User without a household: no cards, a link to `/household`, no failing requests (Task 4).
- One card failing (e.g. `/api/infra` 500) leaves the other two rendered (Task 4).
- First deploy: collector starts before the migration ran; the pass fails, logs, and the next one succeeds (Task 5).

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/models/infra.py` (new) | `InfraStatus`, `BackupStatus` ORM models |
| `backend/migrations/versions/0005_infra_status.py` (new) | creates both tables |
| `backend/app/repositories/infra.py` (new) | writes (collector) and latest-per-instance reads (API) |
| `backend/app/core/config.py` | `PortainerInstance`, collector and backup settings |
| `backend/app/core/clock.py` | `now()` / `Now` dependency (datetime) |
| `backend/app/collector/portainer.py` (new) | `check()`: one instance → `Reading` |
| `backend/app/collector/backups.py` (new) | `newest()`: latest archive of an instance in the bucket |
| `backend/app/collector/main.py` (new) | `collect_once()` and the `run()` loop |
| `backend/app/collector/__main__.py` (new) | `python -m app.collector` entry |
| `backend/app/api/routes/infra.py` (new) | `GET /api/infra` |
| `frontend/src/api/infra.ts`, `stores/infra.ts` (new) | typed call, Pinia store |
| `frontend/src/format.ts` | `bytes()`, `age()` helpers |
| `frontend/src/views/HomeView.vue` | the three-card dashboard |
| `deploy/portainer-stack.yml`, `scripts/portainer-stack.sh`, `.github/workflows/deploy.yml`, `.portainer.env.example` | the collector service and its two stack variables |
| `docs/dashboard.md` (new), `README.md`, `docs/testing.md` | documentation |

---

### Task 1: Infra tables and repository

**Files:**
- Create: `backend/app/models/infra.py`, `backend/migrations/versions/0005_infra_status.py`, `backend/app/repositories/infra.py`, `backend/tests/integration/test_infra_repository.py`
- Modify: `backend/app/models/__init__.py`, `backend/tests/conftest.py`, `backend/.coveragerc.ci`

**Interfaces:**
- Produces:
  - `InfraStatus(id, instance, checked_at, reachable, version, environments, stacks, error)`; `BackupStatus(id, instance, checked_at, last_backup_at, size_bytes, object_key)`
  - `InfraRepository.add_status(*, instance: str, reachable: bool, version: str | None = None, environments: int | None = None, stacks: int | None = None, error: str | None = None, checked_at: datetime | None = None) -> InfraStatus`
  - `InfraRepository.add_backup(*, instance: str, last_backup_at: datetime | None, size_bytes: int | None, object_key: str | None, checked_at: datetime | None = None) -> BackupStatus`
  - `InfraRepository.latest_statuses() -> list[InfraStatus]`, `latest_backups() -> list[BackupStatus]` (newest row per instance, ordered by instance)
  - `InfraRepository.prune(before: datetime) -> int` (rows deleted from both tables)
  - `infra_repository(db) -> InfraRepository`; `InfraRepo = Annotated[InfraRepository, Depends(infra_repository)]`
  - test fixtures: `FakeInfraRepository` (same method names), fixture `infra` that overrides `infra_repository`

- [ ] **Step 1: Write the integration test (fails: module missing)**

`backend/tests/integration/test_infra_repository.py`:

```python
"""Integration — InfraRepository against real PostgreSQL."""

from datetime import UTC, datetime, timedelta

import pytest

from app.repositories.infra import InfraRepository

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def infra(db):
    return InfraRepository(db)


def test_latest_statuses_keeps_the_newest_row_per_instance(infra):
    infra.add_status(instance="heaven", reachable=False, error="boom", checked_at=T0)
    infra.add_status(instance="heaven", reachable=True, version="2.21.0", checked_at=T0 + timedelta(minutes=5))
    infra.add_status(instance="infra", reachable=True, version="2.20.0", checked_at=T0)

    latest = infra.latest_statuses()

    assert [(s.instance, s.reachable, s.version) for s in latest] == [
        ("heaven", True, "2.21.0"),
        ("infra", True, "2.20.0"),
    ]


def test_latest_backups_keeps_the_newest_row_per_instance(infra):
    infra.add_backup(instance="heaven", last_backup_at=None, size_bytes=None, object_key=None, checked_at=T0)
    infra.add_backup(instance="heaven", last_backup_at=T0, size_bytes=10, object_key="heaven/a", checked_at=T0 + timedelta(minutes=5))

    (row,) = infra.latest_backups()

    assert (row.instance, row.size_bytes, row.object_key) == ("heaven", 10, "heaven/a")


def test_prune_deletes_old_rows_from_both_tables(infra):
    infra.add_status(instance="heaven", reachable=True, checked_at=T0 - timedelta(days=40))
    infra.add_status(instance="heaven", reachable=True, checked_at=T0)
    infra.add_backup(instance="heaven", last_backup_at=None, size_bytes=None, object_key=None, checked_at=T0 - timedelta(days=40))

    assert infra.prune(T0 - timedelta(days=30)) == 2
    assert len(infra.latest_statuses()) == 1
    assert infra.latest_backups() == []
```

Check how the existing `db` fixture is provided (`grep -n "def db" backend/tests/integration/conftest.py`); the households test uses it the same way.

- [ ] **Step 2: Run it to see it fail**

Run: `cd backend && .venv/bin/python -m pytest tests/integration/test_infra_repository.py -v`
Expected: collection error `ModuleNotFoundError: app.repositories.infra` (or skipped if services are down: run `make services-test-up` first).

- [ ] **Step 3: Models**

`backend/app/models/infra.py`:

```python
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.files import Base


class InfraStatus(Base):
    """One collector reading of a Portainer instance. Append-only."""

    __tablename__ = "infra_status"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instance: Mapped[str] = mapped_column(Text, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reachable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
    environments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stacks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_infra_status_instance_checked_at", "instance", "checked_at"),)


class BackupStatus(Base):
    """The newest archive of an instance as the collector saw it. Append-only."""

    __tablename__ = "backup_status"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instance: Mapped[str] = mapped_column(Text, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Null = the bucket holds no archive for this instance.
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_backup_status_instance_checked_at", "instance", "checked_at"),)
```

In `backend/app/models/__init__.py` add `from app.models.infra import BackupStatus, InfraStatus` (keep isort order: after `files`, before `households`) and add `"BackupStatus"` and `"InfraStatus"` to `__all__` in sorted position.

- [ ] **Step 4: Migration**

`backend/migrations/versions/0005_infra_status.py`:

```python
"""infra_status and backup_status: the collector's readings.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _checked_at() -> sa.Column:
    return sa.Column(
        "checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "infra_status",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instance", sa.Text(), nullable=False),
        _checked_at(),
        sa.Column("reachable", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Text()),
        sa.Column("environments", sa.Integer()),
        sa.Column("stacks", sa.Integer()),
        sa.Column("error", sa.Text()),
    )
    op.create_index(
        "ix_infra_status_instance_checked_at", "infra_status", ["instance", "checked_at"]
    )
    op.create_table(
        "backup_status",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instance", sa.Text(), nullable=False),
        _checked_at(),
        sa.Column("last_backup_at", sa.DateTime(timezone=True)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("object_key", sa.Text()),
    )
    op.create_index(
        "ix_backup_status_instance_checked_at", "backup_status", ["instance", "checked_at"]
    )


def downgrade() -> None:
    op.drop_table("backup_status")
    op.drop_table("infra_status")
```

Check the previous revision's tail (`tail -20 backend/migrations/versions/0004_providers_services_invoices.py`) and mirror its `downgrade` style if it differs.

- [ ] **Step 5: Repository**

`backend/app/repositories/infra.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.infra import BackupStatus, InfraStatus


class InfraRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add_status(
        self,
        *,
        instance: str,
        reachable: bool,
        version: str | None = None,
        environments: int | None = None,
        stacks: int | None = None,
        error: str | None = None,
        checked_at: datetime | None = None,
    ) -> InfraStatus:
        row = InfraStatus(
            instance=instance,
            reachable=reachable,
            version=version,
            environments=environments,
            stacks=stacks,
            error=error,
            checked_at=checked_at or datetime.now(UTC),
        )
        self.db.add(row)
        self.db.commit()
        return row

    def add_backup(
        self,
        *,
        instance: str,
        last_backup_at: datetime | None,
        size_bytes: int | None,
        object_key: str | None,
        checked_at: datetime | None = None,
    ) -> BackupStatus:
        row = BackupStatus(
            instance=instance,
            last_backup_at=last_backup_at,
            size_bytes=size_bytes,
            object_key=object_key,
            checked_at=checked_at or datetime.now(UTC),
        )
        self.db.add(row)
        self.db.commit()
        return row

    def latest_statuses(self) -> list[InfraStatus]:
        statement = (
            select(InfraStatus)
            .distinct(InfraStatus.instance)
            .order_by(InfraStatus.instance, InfraStatus.checked_at.desc(), InfraStatus.id.desc())
        )
        return list(self.db.scalars(statement))

    def latest_backups(self) -> list[BackupStatus]:
        statement = (
            select(BackupStatus)
            .distinct(BackupStatus.instance)
            .order_by(BackupStatus.instance, BackupStatus.checked_at.desc(), BackupStatus.id.desc())
        )
        return list(self.db.scalars(statement))

    def prune(self, before: datetime) -> int:
        deleted = 0
        for model in (InfraStatus, BackupStatus):
            deleted += self.db.execute(delete(model).where(model.checked_at < before)).rowcount
        self.db.commit()
        return deleted


def infra_repository(db: Db) -> InfraRepository:
    return InfraRepository(db)


InfraRepo = Annotated[InfraRepository, Depends(infra_repository)]
```

- [ ] **Step 6: Fake repository and fixture in `backend/tests/conftest.py`**

Add imports `from datetime import UTC, date, datetime` (extend the existing `datetime` import line), `from app.models.infra import BackupStatus, InfraStatus`, `from app.repositories.infra import infra_repository`. Append after the `clock` fixture:

```python
class FakeInfraRepository:
    """The slice of InfraRepository the routes and the collector use, over lists.

    Real model objects, like the other fakes, so column names cannot drift."""

    def __init__(self):
        self.statuses: list[InfraStatus] = []
        self.backups: list[BackupStatus] = []

    def add_status(
        self, *, instance, reachable, version=None, environments=None, stacks=None, error=None,
        checked_at=None,
    ):
        row = InfraStatus(
            instance=instance, reachable=reachable, version=version, environments=environments,
            stacks=stacks, error=error, checked_at=checked_at or datetime.now(UTC),
        )
        self.statuses.append(row)
        return row

    def add_backup(self, *, instance, last_backup_at, size_bytes, object_key, checked_at=None):
        row = BackupStatus(
            instance=instance, last_backup_at=last_backup_at, size_bytes=size_bytes,
            object_key=object_key, checked_at=checked_at or datetime.now(UTC),
        )
        self.backups.append(row)
        return row

    @staticmethod
    def _latest(rows):
        newest = {}
        for row in rows:
            if row.instance not in newest or row.checked_at >= newest[row.instance].checked_at:
                newest[row.instance] = row
        return [newest[name] for name in sorted(newest)]

    def latest_statuses(self):
        return self._latest(self.statuses)

    def latest_backups(self):
        return self._latest(self.backups)

    def prune(self, before):
        count = len(self.statuses) + len(self.backups)
        self.statuses = [r for r in self.statuses if r.checked_at >= before]
        self.backups = [r for r in self.backups if r.checked_at >= before]
        return count - len(self.statuses) - len(self.backups)


@pytest.fixture
def infra():
    """Swap the infra repository for an in-memory fake. Explicit, never autouse."""
    fake = FakeInfraRepository()
    app.dependency_overrides[infra_repository] = lambda: fake
    yield fake
    app.dependency_overrides.pop(infra_repository, None)
```

- [ ] **Step 7: Exclude the real repository from the service-less coverage gate**

Append `    app/repositories/infra.py` to the `omit` list in `backend/.coveragerc.ci`, and add `test_infra_repository.py` to the list of gating tests in that file's comment.

- [ ] **Step 8: Run tests**

Run: `make services-test-up && cd backend && .venv/bin/python -m pytest tests/integration/test_infra_repository.py -v && .venv/bin/python -m pytest -m unit -q && .venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check .`
Expected: 3 integration tests pass (migration 0005 applied by the suite's schema fixture), unit suite unchanged and green, ruff clean. If `ruff format --check` complains about the long lines in the new test, run `.venv/bin/python -m ruff format .`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models backend/migrations backend/app/repositories/infra.py backend/tests backend/.coveragerc.ci
git commit -m "feat: infra_status and backup_status tables with repository"
```

---

### Task 2: Collector

**Files:**
- Create: `backend/app/collector/__init__.py` (empty), `backend/app/collector/portainer.py`, `backend/app/collector/backups.py`, `backend/app/collector/main.py`, `backend/app/collector/__main__.py`, `backend/tests/unit/test_collector.py`
- Modify: `backend/app/core/config.py`, `backend/pyproject.toml`

**Interfaces:**
- Consumes: `InfraRepository` methods from Task 1 (the fake in tests).
- Produces:
  - `config.PortainerInstance(name: str, url: str, api_key: str, insecure: bool = False)`; `Settings.portainer_instances: list[PortainerInstance]`
  - `Settings.backup_s3_endpoint/backup_s3_secure/backup_s3_bucket/backup_s3_access_key/backup_s3_secret_key`, `backup_max_age_hours: int = 48`, `collector_interval_seconds: int = 300`
  - `portainer.slug(name: str) -> str`
  - `portainer.Reading(reachable: bool, version: str | None = None, environments: int | None = None, stacks: int | None = None, error: str | None = None)` (frozen dataclass)
  - `portainer.check(instance: PortainerInstance, transport: httpx.BaseTransport | None = None) -> Reading` (never raises for network or payload problems)
  - `backups.Archive(last_backup_at: datetime, size_bytes: int, object_key: str)`; `backups.newest(client, bucket: str, instance: str) -> Archive | None` (`client` is a `minio.Minio`)
  - `main.collect_once(repo, instances: list[PortainerInstance], backup_client, bucket: str, now: datetime | None = None) -> None`; `main.run() -> None`

- [ ] **Step 1: Runtime dependency and settings**

In `backend/pyproject.toml`: add `"httpx>=0.27",` to `dependencies` (after `defusedxml`) and remove `"httpx>=0.27",` from the `dev` extras (it is now a runtime dependency). Then `cd backend && uv pip install -e ".[dev]"` (see `make install-backend`).

In `backend/app/core/config.py` add `from pydantic import BaseModel` and, above `Settings`:

```python
class PortainerInstance(BaseModel):
    """One Portainer the collector polls. `name` becomes the bucket prefix the
    backup script writes to (lower-cased, `_` to `-`)."""

    name: str
    url: str
    api_key: str
    insecure: bool = False  # self-signed certificate
```

and inside `Settings`, after the `ollama_model` line:

```python
    # The collector (python -m app.collector). It is the only process that gets
    # Portainer API keys -- Docker-root tokens -- so darkangel-api leaves
    # `portainer_instances` empty. A JSON list in the environment.
    portainer_instances: list[PortainerInstance] = []
    # The archives scripts/portainer-backup.sh writes, read with a read-only
    # identity (Infra: `make s3-provision`, see docs/dashboard.md).
    backup_s3_endpoint: str = "s3:8333"
    backup_s3_secure: bool = False
    backup_s3_bucket: str = "portainer-backups"
    backup_s3_access_key: str = "portainer-backups-ro"
    backup_s3_secret_key: str = ""
    # Older than this = the dashboard marks the backup stale. Read by the API.
    backup_max_age_hours: int = 48
    collector_interval_seconds: int = 300
```

- [ ] **Step 2: Write the failing tests**

`backend/tests/unit/test_collector.py`:

```python
"""Unit — the collector, against a fake Portainer (httpx.MockTransport) and a fake bucket."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx

from app.collector import backups, main, portainer
from app.core.config import PortainerInstance
from tests.conftest import FakeInfraRepository

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
HEAVEN = PortainerInstance(name="HEAVEN", url="https://heaven.example", api_key="sekret-key")


def portainer_api(**overrides):
    routes = {
        "/api/system/status": {"Version": "2.21.0"},
        "/api/endpoints": [{"Id": 1}, {"Id": 2}],
        "/api/stacks": [{"Id": 1}, {"Id": 2}, {"Id": 3}],
    } | overrides

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["X-API-KEY"] == "sekret-key"
        body = routes[request.url.path]
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def test_slug_matches_the_backup_script():
    assert portainer.slug("MY_BOX2") == "my-box2"


def test_a_healthy_instance_reports_version_environments_and_stacks():
    reading = portainer.check(HEAVEN, portainer_api())

    assert reading == portainer.Reading(True, "2.21.0", 2, 3)


def test_a_bad_status_code_is_unreachable_and_the_key_stays_out_of_the_error():
    reading = portainer.check(HEAVEN, portainer_api(**{"/api/stacks": httpx.Response(401)}))

    assert reading.reachable is False
    assert "401" in reading.error
    assert "sekret-key" not in reading.error


def test_a_page_instead_of_json_is_unreachable():
    page = httpx.Response(200, text="<html>SSO login</html>")

    reading = portainer.check(HEAVEN, portainer_api(**{"/api/system/status": page}))

    assert reading.reachable is False


def test_an_object_where_a_list_is_expected_is_unreachable():
    reading = portainer.check(HEAVEN, portainer_api(**{"/api/endpoints": {"message": "nope"}}))

    assert reading.reachable is False


def test_a_connection_failure_is_unreachable():
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    reading = portainer.check(HEAVEN, httpx.MockTransport(refuse))

    assert reading.reachable is False
    assert "ConnectError" in reading.error


class FakeBucket:
    def __init__(self, objects=(), fail=None):
        self.objects, self.fail, self.prefixes = list(objects), fail, []

    def list_objects(self, bucket, prefix=None, recursive=False):
        self.prefixes.append(prefix)
        if self.fail:
            raise self.fail
        return iter(self.objects)


def obj(key, when, size=100, is_dir=False):
    return SimpleNamespace(object_name=key, last_modified=when, size=size, is_dir=is_dir)


def test_newest_picks_the_latest_archive_under_the_instance_prefix():
    bucket = FakeBucket([
        obj("heaven/20261008T000000Z.tar.gz.encrypted", NOW - timedelta(days=2), 90),
        obj("heaven/20261010T000000Z.tar.gz.encrypted", NOW - timedelta(hours=12), 120),
        obj("heaven/sub/", NOW, is_dir=True),
    ])

    archive = backups.newest(bucket, "portainer-backups", "heaven")

    assert archive == backups.Archive(
        NOW - timedelta(hours=12), 120, "heaven/20261010T000000Z.tar.gz.encrypted"
    )
    assert bucket.prefixes == ["heaven/"]


def test_newest_is_none_for_an_empty_prefix():
    assert backups.newest(FakeBucket(), "portainer-backups", "heaven") is None


def run_once(repo, instances, bucket, monkeypatch, check):
    monkeypatch.setattr(portainer, "check", check)
    main.collect_once(repo, instances, bucket, "portainer-backups", now=NOW)


def test_collect_once_records_status_and_backup_per_instance(monkeypatch):
    repo = FakeInfraRepository()
    bucket = FakeBucket([obj("heaven/a.tar.gz.encrypted", NOW - timedelta(hours=3), 500)])

    run_once(repo, [HEAVEN], bucket, monkeypatch, lambda inst: portainer.Reading(True, "2.21.0", 2, 3))

    (status,) = repo.statuses
    (backup,) = repo.backups
    assert (status.instance, status.reachable, status.version, status.checked_at) == (
        "heaven", True, "2.21.0", NOW,
    )
    assert (backup.instance, backup.size_bytes, backup.object_key) == (
        "heaven", 500, "heaven/a.tar.gz.encrypted",
    )


def test_one_unreachable_instance_does_not_stop_the_next(monkeypatch):
    repo = FakeInfraRepository()
    infra = PortainerInstance(name="INFRA", url="https://infra.example", api_key="k")

    def check(inst):
        if inst.name == "HEAVEN":
            return portainer.Reading(False, error="ConnectError: refused")
        return portainer.Reading(True, "2.20.0", 1, 1)

    run_once(repo, [HEAVEN, infra], FakeBucket(), monkeypatch, check)

    assert [(s.instance, s.reachable) for s in repo.statuses] == [("heaven", False), ("infra", True)]


def test_an_unreadable_bucket_records_no_backup_and_still_records_the_status(monkeypatch):
    repo = FakeInfraRepository()
    bucket = FakeBucket(fail=RuntimeError("s3 down"))

    run_once(repo, [HEAVEN], bucket, monkeypatch, lambda inst: portainer.Reading(True, "2.21.0", 1, 1))

    assert len(repo.statuses) == 1
    (backup,) = repo.backups
    assert backup.last_backup_at is None


def test_collect_once_prunes_rows_older_than_thirty_days(monkeypatch):
    repo = FakeInfraRepository()
    repo.add_status(instance="heaven", reachable=True, checked_at=NOW - timedelta(days=31))
    repo.add_backup(instance="heaven", last_backup_at=None, size_bytes=None, object_key=None,
                    checked_at=NOW - timedelta(days=31))

    run_once(repo, [], FakeBucket(), monkeypatch, lambda inst: None)

    assert repo.statuses == [] and repo.backups == []
```

- [ ] **Step 3: Run to see them fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_collector.py -v`
Expected: collection error, `No module named 'app.collector'`.

- [ ] **Step 4: Implement `portainer.py`**

`backend/app/collector/portainer.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.config import PortainerInstance


def slug(name: str) -> str:
    """The bucket prefix scripts/portainer-backup.sh uses for an instance."""
    return name.lower().replace("_", "-")


@dataclass(frozen=True)
class Reading:
    reachable: bool
    version: str | None = None
    environments: int | None = None
    stacks: int | None = None
    error: str | None = None


def _get(client: httpx.Client, path: str):
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def _count(client: httpx.Client, path: str) -> int:
    body = _get(client, path)
    if not isinstance(body, list):
        raise ValueError(f"{path} did not return a list")
    return len(body)


def check(instance: PortainerInstance, transport: httpx.BaseTransport | None = None) -> Reading:
    """Read-only look at one Portainer. Anything wrong with the instance or its
    reply becomes `reachable=False` plus a short message; it never raises."""
    try:
        with httpx.Client(
            base_url=instance.url.rstrip("/"),
            headers={"X-API-KEY": instance.api_key},
            timeout=10.0,
            verify=not instance.insecure,
            transport=transport,
        ) as client:
            version = str(_get(client, "/api/system/status")["Version"])
            environments = _count(client, "/api/endpoints")
            stacks = _count(client, "/api/stacks")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        # httpx messages carry the URL, never request headers, so the key stays out.
        return Reading(False, error=f"{type(e).__name__}: {e}"[:200])
    return Reading(True, version, environments, stacks)
```

- [ ] **Step 5: Implement `backups.py`**

`backend/app/collector/backups.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from minio import Minio


@dataclass(frozen=True)
class Archive:
    last_backup_at: datetime
    size_bytes: int
    object_key: str


def newest(client: Minio, bucket: str, instance: str) -> Archive | None:
    """The most recent object under `<instance>/`, or None when there is none."""
    objects = [
        o
        for o in client.list_objects(bucket, prefix=f"{instance}/", recursive=True)
        if not o.is_dir
    ]
    if not objects:
        return None
    latest = max(objects, key=lambda o: o.last_modified)
    return Archive(latest.last_modified, latest.size, latest.object_name)
```

- [ ] **Step 6: Implement `main.py` and `__main__.py`**

`backend/app/collector/main.py`:

```python
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from minio import Minio

from app.collector import backups, portainer
from app.core.config import PortainerInstance, get_settings
from app.core.db import session_factory
from app.repositories.infra import InfraRepository

log = logging.getLogger("app.collector")

RETENTION = timedelta(days=30)


def collect_once(
    repo: InfraRepository,
    instances: list[PortainerInstance],
    backup_client: Minio,
    bucket: str,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    for instance in instances:
        name = portainer.slug(instance.name)
        reading = portainer.check(instance)
        repo.add_status(
            instance=name,
            reachable=reading.reachable,
            version=reading.version,
            environments=reading.environments,
            stacks=reading.stacks,
            error=reading.error,
            checked_at=now,
        )
        try:
            archive = backups.newest(backup_client, bucket, name)
        except Exception:
            # An unreadable bucket reads as "no backup" until the next pass.
            log.exception("could not list backups of %s", name)
            archive = None
        repo.add_backup(
            instance=name,
            last_backup_at=archive.last_backup_at if archive else None,
            size_bytes=archive.size_bytes if archive else None,
            object_key=archive.object_key if archive else None,
            checked_at=now,
        )
    repo.prune(now - RETENTION)


def run() -> None:
    settings = get_settings()
    if not settings.portainer_instances:
        log.warning("no portainer_instances configured; the collector only idles")
    client = Minio(
        settings.backup_s3_endpoint,
        access_key=settings.backup_s3_access_key,
        secret_key=settings.backup_s3_secret_key,
        secure=settings.backup_s3_secure,
    )
    while True:
        try:
            with session_factory()() as db:
                collect_once(
                    InfraRepository(db),
                    settings.portainer_instances,
                    client,
                    settings.backup_s3_bucket,
                )
        except Exception:
            # Database down, or the tables do not exist yet (first deploy: the
            # API migrates, the collector may start first). The next pass retries.
            log.exception("collection pass failed")
        time.sleep(settings.collector_interval_seconds)
```

`backend/app/collector/__main__.py`:

```python
import logging

from app.collector.main import run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
run()
```

- [ ] **Step 7: Run tests and lint**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_collector.py -v && .venv/bin/python -m pytest -m "unit or regression" -q && .venv/bin/python -m ruff format . && .venv/bin/python -m ruff check .`
Expected: all collector tests pass; the rest of the suite is green; ruff clean.

- [ ] **Step 8: Verify the settings parse JSON from the environment**

Run: `cd backend && DARKANGEL_PORTAINER_INSTANCES='[{"name":"HEAVEN","url":"https://h.example","api_key":"k"}]' .venv/bin/python -c "from app.core.config import Settings; print(Settings().portainer_instances)"`
Expected: `[PortainerInstance(name='HEAVEN', url='https://h.example', api_key='k', insecure=False)]`

- [ ] **Step 9: Commit**

```bash
git add backend/app/collector backend/app/core/config.py backend/pyproject.toml backend/tests/unit/test_collector.py
git commit -m "feat: collector polls Portainer instances and the backup bucket"
```

---

### Task 3: `GET /api/infra`

**Files:**
- Create: `backend/app/api/routes/infra.py`, `backend/tests/unit/test_infra_routes.py`
- Modify: `backend/app/core/clock.py`, `backend/app/api/router.py`, `backend/tests/regression/openapi.snapshot.json` (via `make snapshot`)

**Interfaces:**
- Consumes: `InfraRepo` (Task 1), `Settings.backup_max_age_hours` (Task 2), `Reader` (`app.core.household`).
- Produces: `GET /api/infra` → `{"instances": [InstanceStatus], "backups": [BackupInfo]}`:
  - `InstanceStatus`: `name: str, reachable: bool, version: str | None, environments: int | None, stacks: int | None, checked_at: datetime, error: str | None`
  - `BackupInfo`: `instance: str, last_backup_at: datetime | None, size_bytes: int | None, age_hours: float | None, stale: bool, checked_at: datetime`
  - `core.clock.now() -> datetime` and `Now` dependency

- [ ] **Step 1: Write the failing tests**

`backend/tests/unit/test_infra_routes.py`:

```python
"""Unit — GET /api/infra: latest readings, and the stale-backup rule."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.clock import now
from app.main import app
from tests.conftest import token

client = TestClient(app)
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


@pytest.fixture(autouse=True)
def pinned_now():
    app.dependency_overrides[now] = lambda: NOW
    yield
    app.dependency_overrides.pop(now, None)


def test_requires_a_token():
    assert client.get("/api/infra").status_code == 401


def test_before_the_first_pass_both_lists_are_empty(home, infra):
    assert client.get("/api/infra", headers=auth()).json() == {"instances": [], "backups": []}


def test_reports_the_latest_reading_per_instance(home, infra):
    infra.add_status(instance="heaven", reachable=False, error="old", checked_at=NOW - timedelta(minutes=10))
    infra.add_status(instance="heaven", reachable=True, version="2.21.0", environments=2, stacks=7,
                     checked_at=NOW - timedelta(minutes=2))

    (heaven,) = client.get("/api/infra", headers=auth()).json()["instances"]

    assert heaven == {
        "name": "heaven", "reachable": True, "version": "2.21.0", "environments": 2, "stacks": 7,
        "checked_at": "2026-10-10T11:58:00Z", "error": None,
    }


def test_a_recent_archive_is_fresh_and_an_old_one_is_stale(home, infra):
    infra.add_backup(instance="heaven", last_backup_at=NOW - timedelta(hours=5), size_bytes=1000,
                     object_key="heaven/a", checked_at=NOW)
    infra.add_backup(instance="infra", last_backup_at=NOW - timedelta(hours=49), size_bytes=2000,
                     object_key="infra/a", checked_at=NOW)

    backups = client.get("/api/infra", headers=auth()).json()["backups"]

    assert [(b["instance"], b["age_hours"], b["stale"]) for b in backups] == [
        ("heaven", 5.0, False),
        ("infra", 49.0, True),
    ]


def test_an_instance_without_any_archive_is_stale(home, infra):
    infra.add_backup(instance="heaven", last_backup_at=None, size_bytes=None, object_key=None,
                     checked_at=NOW)

    (backup,) = client.get("/api/infra", headers=auth()).json()["backups"]

    assert (backup["last_backup_at"], backup["age_hours"], backup["stale"]) == (None, None, True)
```

If the home fixture's `user-1` does not give a household to a request made with the `auth()` token, check `tests/unit/test_costs_routes.py::auth` — it uses the same `sub="user-1"`.

- [ ] **Step 2: Run to see them fail**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_infra_routes.py -v`
Expected: `ImportError: cannot import name 'now' from 'app.core.clock'`.

- [ ] **Step 3: `now` dependency**

`backend/app/core/clock.py` becomes:

```python
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import Depends


def today() -> date:
    """A dependency so tests can pin the date instead of patching `datetime`."""
    return date.today()


def now() -> datetime:
    """Same idea for the instant: ages in hours need more than the date."""
    return datetime.now(UTC)


Today = Annotated[date, Depends(today)]
Now = Annotated[datetime, Depends(now)]
```

- [ ] **Step 4: The route**

`backend/app/api/routes/infra.py`:

```python
from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.clock import Now
from app.core.config import get_settings
from app.core.household import Reader
from app.models.infra import BackupStatus
from app.repositories.infra import InfraRepo

router = APIRouter(tags=["infra"])


class InstanceStatus(BaseModel):
    name: str
    reachable: bool
    version: str | None
    environments: int | None
    stacks: int | None
    checked_at: datetime
    error: str | None


class BackupInfo(BaseModel):
    instance: str
    last_backup_at: datetime | None
    size_bytes: int | None
    age_hours: float | None
    stale: bool
    checked_at: datetime


class Infra(BaseModel):
    instances: list[InstanceStatus]
    backups: list[BackupInfo]


def _backup(row: BackupStatus, now: datetime, max_age_hours: int) -> BackupInfo:
    age = None if row.last_backup_at is None else (now - row.last_backup_at).total_seconds() / 3600
    return BackupInfo(
        instance=row.instance,
        last_backup_at=row.last_backup_at,
        size_bytes=row.size_bytes,
        age_hours=None if age is None else round(age, 1),
        # No archive at all counts as stale.
        stale=age is None or age > max_age_hours,
        checked_at=row.checked_at,
    )


@router.get("/infra", response_model=Infra)
def infra(ctx: Reader, repo: InfraRepo, now: Now) -> Infra:
    max_age = get_settings().backup_max_age_hours
    return Infra(
        instances=[
            InstanceStatus(
                name=s.instance,
                reachable=s.reachable,
                version=s.version,
                environments=s.environments,
                stacks=s.stacks,
                checked_at=s.checked_at,
                error=s.error,
            )
            for s in repo.latest_statuses()
        ],
        backups=[_backup(b, now, max_age) for b in repo.latest_backups()],
    )
```

In `backend/app/api/router.py` change the routes import to `from app.api.routes import files, folders, health, household, infra, invoices, me, providers` and append `api_router.include_router(infra.router)`.

- [ ] **Step 5: Run the new tests**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/test_infra_routes.py -v`
Expected: PASS (5 tests). If the `checked_at` string differs (`+00:00` vs `Z`), assert on what FastAPI actually emits for the other routes' datetimes (`grep -rn "Z\"" tests/unit | head`) and fix the test, not the route.

- [ ] **Step 6: Pin the contract**

Run: `make test-regression` and expect `test_openapi_matches_the_snapshot` to FAIL (new path). Then `make snapshot` and `git diff --stat backend/tests/regression/openapi.snapshot.json`; read the diff: it must add only `/api/infra` and its three schemas. Rerun `make test-regression`: PASS.

- [ ] **Step 7: Full backend gate**

Run: `cd backend && .venv/bin/python -m pytest -m "unit or regression" --cov=app --cov-config=.coveragerc.ci --cov-fail-under=80 -q && .venv/bin/python -m ruff format --check . && .venv/bin/python -m ruff check .`
Expected: green, coverage above 80.

- [ ] **Step 8: Commit**

```bash
git add backend/app backend/tests
git commit -m "feat: GET /api/infra with the stale-backup rule"
```

---

### Task 4: Frontend dashboard

**Files:**
- Create: `frontend/src/api/infra.ts`, `frontend/src/stores/infra.ts`, `frontend/tests/unit/format-infra.test.ts`, `frontend/tests/unit/stores-infra.test.ts`, `frontend/tests/unit/HomeView.test.ts`
- Modify: `frontend/src/format.ts`, `frontend/src/views/HomeView.vue`

**Interfaces:**
- Consumes: `GET /api/infra` (Task 3); existing `useHouseholdStore` (`household`, `loaded`, `error`, `load()`), `useInvoicesStore` (`upcoming`, `toReview`, `error`, `loadDashboard()`), `useHealthStore`, `useMeStore`; `money()` from `@/format`.
- Produces:
  - `api/infra.ts`: types `InstanceStatus`, `BackupInfo`, `Infra`; `getInfra(): Promise<Infra>`
  - `useInfraStore()`: `instances`, `backups`, `error`, `loading`, `collectorStale` (computed boolean), `load()`
  - `format.ts`: `bytes(n)`, `age(hours)`

- [ ] **Step 1: Helper tests (fail)**

`frontend/tests/unit/format-infra.test.ts`:

```ts
import { expect, it } from 'vitest'

import { age, bytes } from '@/format'

it('formats sizes', () => {
  expect(bytes(null)).toBe('—')
  expect(bytes(512)).toBe('512 B')
  expect(bytes(1536)).toBe('1.5 KB')
  expect(bytes(5 * 1024 ** 3)).toBe('5.0 GB')
})

it('formats ages in hours then days', () => {
  expect(age(null)).toBe('—')
  expect(age(0.4)).toBe('< 1 h')
  expect(age(5.2)).toBe('5 h')
  expect(age(49)).toBe('2 d')
})
```

Run: `cd frontend && npx vitest run tests/unit/format-infra.test.ts` — Expected: FAIL (`bytes` is not exported).

- [ ] **Step 2: Helpers**

Append to `frontend/src/format.ts`:

```ts
/** File size in 1024-based units. */
export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = n
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${i === 0 ? value : value.toFixed(1)} ${units[i]}`
}

/** An age given in hours: "< 1 h", "5 h", then days from 48 h on. */
export function age(hours: number | null | undefined): string {
  if (hours === null || hours === undefined) return '—'
  if (hours < 1) return '< 1 h'
  if (hours < 48) return `${Math.round(hours)} h`
  return `${Math.round(hours / 24)} d`
}
```

Run the test again: PASS.

- [ ] **Step 3: API module and store tests (fail)**

`frontend/src/api/infra.ts`:

```ts
import { apiGet } from './client'

export interface InstanceStatus {
  name: string
  reachable: boolean
  version: string | null
  environments: number | null
  stacks: number | null
  checked_at: string
  error: string | null
}

export interface BackupInfo {
  instance: string
  last_backup_at: string | null
  size_bytes: number | null
  age_hours: number | null
  stale: boolean
  checked_at: string
}

export interface Infra {
  instances: InstanceStatus[]
  backups: BackupInfo[]
}

export function getInfra(): Promise<Infra> {
  return apiGet<Infra>('/infra')
}
```

`frontend/tests/unit/stores-infra.test.ts`:

```ts
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { getInfra } from '@/api/infra'
import { useInfraStore } from '@/stores/infra'

vi.mock('@/api/infra', () => ({ getInfra: vi.fn() }))

const NOW = new Date('2026-10-10T12:00:00Z')
const reading = (checked_at: string) => ({
  name: 'heaven', reachable: true, version: '2.21.0', environments: 1, stacks: 2,
  checked_at, error: null,
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})
afterEach(() => vi.useRealTimers())

it('load() stores both lists', async () => {
  vi.mocked(getInfra).mockResolvedValue({ instances: [reading('2026-10-10T11:58:00Z')], backups: [] })
  const store = useInfraStore()

  await store.load()

  expect(store.instances).toHaveLength(1)
  expect(store.error).toBeNull()
})

it('load() records the error', async () => {
  vi.mocked(getInfra).mockRejectedValue(new Error('GET /infra failed with 500'))
  const store = useInfraStore()

  await store.load()

  expect(store.error).toBe('GET /infra failed with 500')
  expect(store.loading).toBe(false)
})

it('collectorStale is false when nothing was collected yet', () => {
  expect(useInfraStore().collectorStale).toBe(false)
})

it('collectorStale is true when the newest reading is older than 15 minutes', async () => {
  vi.mocked(getInfra).mockResolvedValue({ instances: [reading('2026-10-10T11:40:00Z')], backups: [] })
  const store = useInfraStore()
  await store.load()

  expect(store.collectorStale).toBe(true)
})

it('collectorStale is false for a reading from two minutes ago', async () => {
  vi.mocked(getInfra).mockResolvedValue({ instances: [reading('2026-10-10T11:58:00Z')], backups: [] })
  const store = useInfraStore()
  await store.load()

  expect(store.collectorStale).toBe(false)
})
```

Run: `npx vitest run tests/unit/stores-infra.test.ts` — Expected: FAIL (`@/stores/infra` missing).

- [ ] **Step 4: Store**

`frontend/src/stores/infra.ts`:

```ts
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { getInfra, type BackupInfo, type InstanceStatus } from '@/api/infra'

// The collector runs every 5 minutes: a reading older than this means it stopped.
const STALE_AFTER_MS = 15 * 60 * 1000

export const useInfraStore = defineStore('infra', () => {
  const instances = ref<InstanceStatus[]>([])
  const backups = ref<BackupInfo[]>([])
  const error = ref<string | null>(null)
  const loading = ref(false)

  const collectorStale = computed(() => {
    const times = [...instances.value, ...backups.value].map((r) => Date.parse(r.checked_at))
    return times.length > 0 && Date.now() - Math.max(...times) > STALE_AFTER_MS
  })

  async function load() {
    loading.value = true
    error.value = null
    try {
      const body = await getInfra()
      instances.value = body.instances
      backups.value = body.backups
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  return { instances, backups, error, loading, collectorStale, load }
})
```

Run: `npx vitest run tests/unit/stores-infra.test.ts` — Expected: PASS. (`collectorStale` is a computed over `Date.now()`, which Vue does not track; the tests above evaluate it after `load()`, which is when the data changes, and the page re-reads it on each render of a fresh load. Do not add a timer: YAGNI.)

- [ ] **Step 5: HomeView tests (fail)**

`frontend/tests/unit/HomeView.test.ts`:

```ts
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import { ApiError } from '@/api/client'
import { getMonthlyCosts, getUpcoming } from '@/api/costs'
import { fetchHealth } from '@/api/health'
import { getHousehold } from '@/api/household'
import { getInfra } from '@/api/infra'
import { listInvoices } from '@/api/invoices'
import { fetchMe } from '@/api/me'
import HomeView from '@/views/HomeView.vue'

vi.mock('@/auth', () => ({ accessToken: vi.fn(async () => null) }))
vi.mock('@/api/health', () => ({ fetchHealth: vi.fn() }))
vi.mock('@/api/me', () => ({ fetchMe: vi.fn() }))
vi.mock('@/api/household', () => ({ getHousehold: vi.fn() }))
vi.mock('@/api/infra', () => ({ getInfra: vi.fn() }))
vi.mock('@/api/invoices', () => ({ listInvoices: vi.fn() }))
vi.mock('@/api/costs', () => ({ getUpcoming: vi.fn(), getMonthlyCosts: vi.fn() }))

const NOW = new Date('2026-10-10T12:00:00Z')
const home = { id: 'h', name: 'Maison', role: 'owner' as const, members: [] }
const fresh = '2026-10-10T11:58:00Z'

function infraBody() {
  return {
    instances: [
      { name: 'heaven', reachable: true, version: '2.21.0', environments: 2, stacks: 7, checked_at: fresh, error: null },
      { name: 'infra', reachable: false, version: null, environments: null, stacks: null, checked_at: fresh, error: 'ConnectError: refused' },
    ],
    backups: [
      { instance: 'heaven', last_backup_at: '2026-10-10T07:00:00Z', size_bytes: 1536, age_hours: 5, stale: false, checked_at: fresh },
      { instance: 'infra', last_backup_at: null, size_bytes: null, age_hours: null, stale: true, checked_at: fresh },
    ],
  }
}

async function render() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: HomeView }, { path: '/:rest(.*)', component: { template: '<p/>' } }],
  })
  await router.push('/')
  const wrapper = mount(HomeView, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'ok', version: '0.1.0' })
  vi.mocked(fetchMe).mockResolvedValue({ sub: 'u1', username: 'nicolas' } as never)
  vi.mocked(getHousehold).mockResolvedValue(home)
  vi.mocked(getInfra).mockResolvedValue(infraBody())
  vi.mocked(listInvoices).mockResolvedValue([])
  vi.mocked(getMonthlyCosts).mockResolvedValue({ months: [] })
  vi.mocked(getUpcoming).mockResolvedValue({
    invoices: [
      { invoice_id: 'i1', service_id: 's1', provider_name: 'Bell', service_name: 'Internet',
        total: '79.99', due_on: '2026-10-01', overdue: true },
    ],
    renewals: [],
  })
})
afterEach(() => vi.useRealTimers())

it('shows a row per instance and per backup, flagging what is wrong', async () => {
  const wrapper = await render()

  const instances = wrapper.findAll('[data-test="instance"]')
  expect(instances.map((i) => i.classes('bad'))).toEqual([false, true])
  expect(instances[0].text()).toContain('2.21.0')
  expect(instances[1].text()).toContain('ConnectError')

  const backups = wrapper.findAll('[data-test="backup"]')
  expect(backups.map((b) => b.classes('bad'))).toEqual([false, true])
  expect(backups[0].text()).toContain('1.5 KB')
  expect(backups[1].text()).toContain('No backup')
})

it('shows the overdue invoice', async () => {
  const wrapper = await render()

  expect(wrapper.find('[data-test="due"]').classes('bad')).toBe(true)
})

it('points a person without a household to the household page and asks nothing else', async () => {
  vi.mocked(getHousehold).mockRejectedValue(new ApiError('no_household', 409, null))

  const wrapper = await render()

  expect(wrapper.find('a[href="/household"]').exists()).toBe(true)
  expect(getInfra).not.toHaveBeenCalled()
  expect(getUpcoming).not.toHaveBeenCalled()
})

it('keeps the other cards when /api/infra fails', async () => {
  vi.mocked(getInfra).mockRejectedValue(new Error('GET /infra failed with 500'))

  const wrapper = await render()

  expect(wrapper.find('[data-test="infra-error"]').text()).toContain('500')
  expect(wrapper.find('[data-test="due"]').exists()).toBe(true)
})

it('warns when the collector stopped', async () => {
  const old = infraBody()
  old.instances[0].checked_at = '2026-10-10T11:00:00Z'
  old.instances[1].checked_at = '2026-10-10T11:00:00Z'
  old.backups.forEach((b) => (b.checked_at = '2026-10-10T11:00:00Z'))
  vi.mocked(getInfra).mockResolvedValue(old)

  const wrapper = await render()

  expect(wrapper.find('[data-test="collector-stale"]').exists()).toBe(true)
})

it('says nothing was collected yet when both lists are empty', async () => {
  vi.mocked(getInfra).mockResolvedValue({ instances: [], backups: [] })

  const wrapper = await render()

  expect(wrapper.text()).toContain('Nothing collected yet')
})
```

Before running, check the names the test assumes: `grep -n "export" frontend/src/api/me.ts` (the function may not be `fetchMe`) and `grep -n "409\|no_household" frontend/src/stores/household.ts` (the 409 must leave `household` null and `loaded` true, as `ProvidersView.test.ts` relies on). Adjust the mock names to what exists.

Run: `npx vitest run tests/unit/HomeView.test.ts` — Expected: FAIL (the old view has no such markup).

- [ ] **Step 6: HomeView**

Replace `frontend/src/views/HomeView.vue`:

```vue
<script setup lang="ts">
import { onMounted } from 'vue'

import { age, bytes, money } from '@/format'
import { useHealthStore } from '@/stores/health'
import { useHouseholdStore } from '@/stores/household'
import { useInfraStore } from '@/stores/infra'
import { useInvoicesStore } from '@/stores/invoices'
import { useMeStore } from '@/stores/me'

const health = useHealthStore()
const me = useMeStore()
const household = useHouseholdStore()
const infra = useInfraStore()
const invoices = useInvoicesStore()

onMounted(async () => {
  void health.load()
  void me.load()
  await household.load()
  // The infra route and the invoice routes both need a household.
  if (household.household) await Promise.all([infra.load(), invoices.loadDashboard()])
})
</script>

<template>
  <section>
    <h1>DarkAngel</h1>
    <p v-if="health.error" class="error">Backend unreachable: {{ health.error }}</p>
    <p v-if="me.error" class="error">API rejected the session: {{ me.error }}</p>
    <p v-else-if="me.me">
      Signed in as <strong>{{ me.me.username ?? me.me.sub }}</strong>
    </p>

    <p v-if="household.error" role="alert" class="error">{{ household.error }}</p>
    <p v-if="household.loaded && !household.household">
      <RouterLink to="/household">Set up your household</RouterLink> to see the dashboard.
    </p>

    <div v-else-if="household.household" class="grid">
      <article>
        <h2>Backups</h2>
        <p v-if="infra.error" class="error" data-test="infra-error">{{ infra.error }}</p>
        <p v-else-if="!infra.backups.length">Nothing collected yet.</p>
        <p v-if="infra.collectorStale" class="error" data-test="collector-stale">
          Data is more than 15 minutes old: is the collector running?
        </p>
        <ul>
          <li
            v-for="b in infra.backups"
            :key="b.instance"
            data-test="backup"
            :class="{ bad: b.stale }"
          >
            <strong>{{ b.instance }}</strong>
            <template v-if="b.last_backup_at">
              {{ age(b.age_hours) }} ago · {{ bytes(b.size_bytes) }}
            </template>
            <template v-else>No backup</template>
          </li>
        </ul>
      </article>

      <article>
        <h2>Infra</h2>
        <p v-if="infra.error" class="error">{{ infra.error }}</p>
        <p v-else-if="!infra.instances.length">Nothing collected yet.</p>
        <ul>
          <li
            v-for="i in infra.instances"
            :key="i.name"
            data-test="instance"
            :class="{ bad: !i.reachable }"
          >
            <strong>{{ i.name }}</strong>
            <template v-if="i.reachable">
              v{{ i.version }} · {{ i.environments }} env · {{ i.stacks }} stacks
            </template>
            <template v-else>Unreachable: {{ i.error }}</template>
          </li>
        </ul>
      </article>

      <article>
        <h2>Invoices</h2>
        <p v-if="invoices.error" class="error">{{ invoices.error }}</p>
        <p v-if="invoices.toReview.length">
          <RouterLink to="/invoices/review">
            {{ invoices.toReview.length }} invoice{{ invoices.toReview.length > 1 ? 's' : '' }} to
            review
          </RouterLink>
        </p>
        <p v-if="!invoices.upcoming.invoices.length && !invoices.upcoming.renewals.length">
          Nothing due.
        </p>
        <ul>
          <li
            v-for="due in invoices.upcoming.invoices.slice(0, 5)"
            :key="due.invoice_id"
            data-test="due"
            :class="{ bad: due.overdue }"
          >
            {{ due.provider_name }} · {{ due.service_name }} · {{ money(due.total) }} ·
            {{ due.due_on }}
          </li>
          <li v-for="r in invoices.upcoming.renewals" :key="r.service_id" data-test="renewal">
            {{ r.provider_name }} · {{ r.service_name }} renews in {{ r.days_left }} d
          </li>
        </ul>
        <RouterLink to="/providers">All providers</RouterLink>
      </article>
    </div>
  </section>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr));
  gap: 1rem;
}
ul {
  padding-left: 1rem;
}
.error,
.bad {
  color: #e06c75;
}
</style>
```

Check `frontend/src/stores/me.ts` exports `me` and `error` with those names (the old view used `meStore.me` / `meStore.error`) and that `RouterLink` needs no import (App.vue uses it bare).

- [ ] **Step 7: Run frontend tests, type-check and build**

Run: `cd frontend && npx vitest run && npm run build`
Expected: all tests pass (previous ones untouched), `vue-tsc` and the build succeed.

- [ ] **Step 8: Look at it**

Start `make dev-backend` and `make dev-frontend` (see `make help`) or use the browser pane on `http://localhost:5173/`; without a Keycloak session the page cannot load, so if login is not possible say so rather than claiming a visual check. The Vitest cases are the verification then.

- [ ] **Step 9: Commit**

```bash
git add frontend/src frontend/tests
git commit -m "feat: home dashboard with backup, infra and invoice cards"
```

---

### Task 5: Deployment and documentation

**Files:**
- Modify: `deploy/portainer-stack.yml`, `scripts/portainer-stack.sh`, `.github/workflows/deploy.yml`, `.portainer.env.example`, `README.md`, `docs/testing.md`
- Create: `docs/dashboard.md`

**Interfaces:**
- Consumes: the `python -m app.collector` entry point and the settings from Task 2.
- Produces: stack variables `PORTAINER_INSTANCES` (JSON list) and `BACKUP_S3_SECRET_KEY`; compose service `darkangel-collector`.

- [ ] **Step 1: Failing selftest for the stack variables**

In `scripts/portainer-stack.sh`, change the `selftest()` expectation first. Replace the `got=`/`want=` pair with:

```bash
  got="$(stack_env nicolaslallier latest s3cret pgpass '[{"name":"HEAVEN"}]' bkey | jq -c .)"
  want='[{"name":"IMAGE_OWNER","value":"nicolaslallier"},{"name":"IMAGE_TAG","value":"latest"},{"name":"S3_SECRET_KEY","value":"s3cret"},{"name":"POSTGRES_PASSWORD","value":"pgpass"},{"name":"PORTAINER_INSTANCES","value":"[{\"name\":\"HEAVEN\"}]"},{"name":"BACKUP_S3_SECRET_KEY","value":"bkey"}]'
```

and the second selftest call `stack_env 'o w' 'sha-1234' '' ''` to `stack_env 'o w' 'sha-1234' '' '' '' ''`.

Run: `scripts/portainer-stack.sh selftest` (check the usage line at the top of the script for the exact subcommand name; `make help | grep -i portainer`).
Expected: FAIL (`selftest: stack_env`).

- [ ] **Step 2: Implement the plumbing**

In `scripts/portainer-stack.sh`:

```bash
stack_env() { # <image-owner> <image-tag> <s3-secret-key> <postgres-password> <portainer-instances> <backup-s3-secret-key>
  jq -n --arg owner "$1" --arg tag "$2" --arg s3 "$3" --arg pg "$4" --arg inst "$5" --arg bk "$6" \
    '[{name: "IMAGE_OWNER", value: $owner},
      {name: "IMAGE_TAG", value: $tag},
      {name: "S3_SECRET_KEY", value: $s3},
      {name: "POSTGRES_PASSWORD", value: $pg},
      {name: "PORTAINER_INSTANCES", value: $inst},
      {name: "BACKUP_S3_SECRET_KEY", value: $bk}]'
}
```

and the call in the `up|pull` branch:

```bash
    [ -n "${PORTAINER_INSTANCES:-}" ] ||
      note "PORTAINER_INSTANCES is not set in .portainer.env: the dashboard's Backups and Infra cards stay empty (see docs/dashboard.md)"
    env="$(stack_env "$IMAGE_OWNER" "$IMAGE_TAG" "${S3_SECRET_KEY:-}" "${POSTGRES_PASSWORD:-}" "${PORTAINER_INSTANCES:-[]}" "${BACKUP_S3_SECRET_KEY:-}")"
```

Update the comment above `stack_env` to say the collector's two values also have to come through here. Run the selftest: PASS. Also run `grep -rn "stack_env" scripts Makefile .github` and fix any other caller.

In `.portainer.env.example` append:

```
# Dashboard collector (python -m app.collector). A JSON list on ONE line, in
# single quotes. Same instances as .portainers.env, but name is the instance
# name in any case and the key is the Portainer access token. Handed to the
# stack by `make up`.
PORTAINER_INSTANCES='[{"name":"HEAVEN","url":"https://portainer.example.net","api_key":"change-me"}]'

# Secret of the READ-ONLY S3 identity portainer-backups-ro on the bucket
# portainer-backups (Infra: make s3-provision, see docs/dashboard.md).
BACKUP_S3_SECRET_KEY=change-me
```

In `.github/workflows/deploy.yml`, after the `POSTGRES_PASSWORD` entry add:

```yaml
          # The dashboard collector's two secrets, same reason: the stack's
          # Env list is rewritten on every deploy.
          PORTAINER_INSTANCES: ${{ secrets.PORTAINER_INSTANCES }}
          BACKUP_S3_SECRET_KEY: ${{ secrets.BACKUP_S3_SECRET_KEY }}
```

- [ ] **Step 3: Collector service**

In `deploy/portainer-stack.yml`, extend the header comment's variable list with `PORTAINER_INSTANCES` and `BACKUP_S3_SECRET_KEY`, and add under `services:` (before `web-assets`):

```yaml
  # Polls the Portainer instances and the portainer-backups bucket every five
  # minutes and writes what it saw to PostgreSQL; the dashboard reads that. It
  # is the only service that holds Portainer API keys (Docker-root tokens).
  # No migration here: darkangel-api runs `alembic upgrade head`. On the very
  # first deploy a pass can fail for want of the tables -- it logs and retries.
  darkangel-collector:
    image: ghcr.io/${IMAGE_OWNER:-nicolaslallier}/darkangel-backend:${IMAGE_TAG:-latest}
    restart: unless-stopped
    command: ["python", "-m", "app.collector"]
    # The image's HEALTHCHECK probes port 8000, which this process never opens.
    healthcheck:
      disable: true
    environment:
      DARKANGEL_DATABASE_URL: postgresql+psycopg://darkangel:${POSTGRES_PASSWORD:-}@postgres:5432/darkangel
      DARKANGEL_PORTAINER_INSTANCES: ${PORTAINER_INSTANCES:-[]}
      DARKANGEL_BACKUP_S3_SECRET_KEY: ${BACKUP_S3_SECRET_KEY:-}
    networks:
      - infra-net
```

Validate: `docker compose -f deploy/portainer-stack.yml config -q` (if Docker is available; set `IMAGE_OWNER` etc. is not required). Expected: no output, exit 0. If Docker is not available, say so.

- [ ] **Step 4: Documentation**

`docs/dashboard.md`: what the page shows; the collector design in one paragraph (polls Portainer GET endpoints and the bucket, append-only tables, 30-day retention, `/api/infra`); the stack variables and their GitHub secrets (`PORTAINER_INSTANCES`, `BACKUP_S3_SECRET_KEY`); a one-time setup: create a Portainer access token per instance, create the read-only S3 identity on the bucket in the Infra repo (`make s3-provision app=portainer-backups-ro bucket=portainer-backups` is the intended shape; confirm the exact flags against the Infra README, which this repo does not contain, and write down what you ran); how to read the colours (stale backup > 48 h, unreachable instance, overdue invoice, data older than 15 min means the collector stopped); and how to debug (`docker logs` of `darkangel-collector`; empty cards = no rows yet).

`README.md`: add a "Dashboard" subsection after "Service providers" (line ~158) of 5 lines linking to `docs/dashboard.md`, and add the two secrets to "Repository secrets" (line ~255). Mirror the style of the existing `S3_SECRET_KEY` bullet.

`docs/testing.md`: update the unit/regression/integration counts and the frontend counts to the numbers `make test` now prints (run it and copy them; do not estimate), and mention `FakeInfraRepository` next to the other fakes in the "Backend unit" row.

- [ ] **Step 5: Whole-repo gate**

Run: `make verify`
Expected: format-check, lint, all test suites and both builds pass (integration needs `make services-test-up`). Report the actual output if anything fails.

- [ ] **Step 6: Commit**

```bash
git add deploy scripts .github .portainer.env.example README.md docs
git commit -m "feat: deploy the collector and document the dashboard"
```

---

## Self-review

- **Spec coverage:** collector (Task 2), tables and 30-day retention (Tasks 1-2), config (Task 2), `GET /api/infra` with stale rule and empty lists (Task 3), three-card UI, per-card errors, stale-collector banner, no-household path (Task 4), stack service, variables, docs, testing counts (Task 5). Visible to every member = `Reader` (Task 3). Nothing in the spec lacks a task.
- **Types:** `Reading`, `Archive`, `add_status`/`add_backup` keyword names, `latest_statuses`/`latest_backups`, `InstanceStatus`/`BackupInfo` fields, `collectorStale`, `bytes`/`age` are spelled identically wherever they recur.
- **Known gaps handed to the reader:** the exact `make s3-provision` flags for the read-only identity live in the Infra repo (Task 5 Step 4 says to confirm them); `PORTAINER_INSTANCES` and `BACKUP_S3_SECRET_KEY` must be created as GitHub repository secrets before the deploy workflow can pass them on.
