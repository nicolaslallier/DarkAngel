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
