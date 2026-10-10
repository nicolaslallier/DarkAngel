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
    infra.add_status(
        instance="heaven", reachable=False, error="old", checked_at=NOW - timedelta(minutes=10)
    )
    infra.add_status(
        instance="heaven",
        reachable=True,
        version="2.21.0",
        environments=2,
        stacks=7,
        checked_at=NOW - timedelta(minutes=2),
    )

    (heaven,) = client.get("/api/infra", headers=auth()).json()["instances"]

    assert heaven == {
        "name": "heaven",
        "reachable": True,
        "version": "2.21.0",
        "environments": 2,
        "stacks": 7,
        "checked_at": "2026-10-10T11:58:00Z",
        "error": None,
    }


def test_a_recent_archive_is_fresh_and_an_old_one_is_stale(home, infra):
    infra.add_backup(
        instance="heaven",
        last_backup_at=NOW - timedelta(hours=5),
        size_bytes=1000,
        object_key="heaven/a",
        checked_at=NOW,
    )
    infra.add_backup(
        instance="infra",
        last_backup_at=NOW - timedelta(hours=49),
        size_bytes=2000,
        object_key="infra/a",
        checked_at=NOW,
    )

    backups = client.get("/api/infra", headers=auth()).json()["backups"]

    assert [(b["instance"], b["age_hours"], b["stale"]) for b in backups] == [
        ("heaven", 5.0, False),
        ("infra", 49.0, True),
    ]


def test_an_instance_without_any_archive_is_stale(home, infra):
    infra.add_backup(
        instance="heaven", last_backup_at=None, size_bytes=None, object_key=None, checked_at=NOW
    )

    (backup,) = client.get("/api/infra", headers=auth()).json()["backups"]

    assert (backup["last_backup_at"], backup["age_hours"], backup["stale"]) == (None, None, True)
