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
    infra.add_status(
        instance="heaven", reachable=True, version="2.21.0", checked_at=T0 + timedelta(minutes=5)
    )
    infra.add_status(instance="infra", reachable=True, version="2.20.0", checked_at=T0)

    latest = infra.latest_statuses()

    assert [(s.instance, s.reachable, s.version) for s in latest] == [
        ("heaven", True, "2.21.0"),
        ("infra", True, "2.20.0"),
    ]


def test_latest_backups_keeps_the_newest_row_per_instance(infra):
    infra.add_backup(
        instance="heaven", last_backup_at=None, size_bytes=None, object_key=None, checked_at=T0
    )
    infra.add_backup(
        instance="heaven",
        last_backup_at=T0,
        size_bytes=10,
        object_key="heaven/a",
        checked_at=T0 + timedelta(minutes=5),
    )

    (row,) = infra.latest_backups()

    assert (row.instance, row.size_bytes, row.object_key) == ("heaven", 10, "heaven/a")


def test_prune_deletes_old_rows_from_both_tables(infra):
    infra.add_status(instance="heaven", reachable=True, checked_at=T0 - timedelta(days=40))
    infra.add_status(instance="heaven", reachable=True, checked_at=T0)
    infra.add_backup(
        instance="heaven",
        last_backup_at=None,
        size_bytes=None,
        object_key=None,
        checked_at=T0 - timedelta(days=40),
    )

    assert infra.prune(T0 - timedelta(days=30)) == 2
    assert len(infra.latest_statuses()) == 1
    assert infra.latest_backups() == []


def test_histories_exclude_rows_before_since_and_run_oldest_first(infra):
    since = T0 - timedelta(hours=1)
    for minutes in (10, -120, 0):
        at = T0 + timedelta(minutes=minutes)
        infra.add_status(instance="heaven", reachable=True, checked_at=at)
        infra.add_backup(
            instance="heaven", last_backup_at=None, size_bytes=1, object_key=None, checked_at=at
        )

    expected = [T0, T0 + timedelta(minutes=10)]
    assert [s.checked_at for s in infra.status_history(since)] == expected
    assert [b.checked_at for b in infra.backup_history(since)] == expected
