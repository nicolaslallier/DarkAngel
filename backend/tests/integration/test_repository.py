"""Integration — the repository's SQL, against a real PostgreSQL."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.models.files import AuditLog, File
from app.repositories.files import FileRepository, QuotaExceeded
from app.repositories.folders import FolderRepository

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
    # BR-9: the bytes are still in MinIO (Phase 1 has no purge), so they still
    # count -- otherwise delete-and-reupload would be an unbounded quota bypass.
    assert repository.used_bytes("user-1") == row.size_bytes


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


def test_find_by_name_never_crosses_owners(repository):
    """R-1, SQL layer: BR-2 versioning routes a same-name upload through
    `find_by_name`, so a missing owner filter here would let one user's
    upload append a version onto another user's file."""
    row = reserve(repository, owner="user-1", name="notes.txt")
    repository.finalize(row, s3_version_id="v", actor_sub="user-1")

    assert repository.find_by_name("user-2", "notes.txt", None) is None
    assert repository.find_by_name("user-1", "notes.txt", None).id == row.id


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


def ready(repository, db, name, owner="user-1", folder_id=None, description=None, tags=()):
    row = repository.reserve(
        owner,
        name=name,
        folder_id=folder_id,
        size_bytes=len(name),
        content_type="text/plain",
        quota_bytes=10**9,
    )
    repository.finalize(row, s3_version_id="v", actor_sub=owner)
    row.description = description
    row.tags = list(tags)
    db.commit()
    return row


def names(rows):
    return [row.name for row in rows]


def test_list_is_scoped_to_one_folder(repository, db):
    folder = FolderRepository(db).create("user-1", name="A", parent_id=None)
    ready(repository, db, "root.txt")
    ready(repository, db, "inside.txt", folder_id=folder.id)

    assert names(repository.list("user-1")) == ["root.txt"]
    assert names(repository.list("user-1", folder_id=folder.id)) == ["inside.txt"]


def test_search_matches_name_description_and_tags_across_folders(repository, db):
    folder = FolderRepository(db).create("user-1", name="A", parent_id=None)
    ready(repository, db, "Tax 2026.pdf")
    ready(repository, db, "scan.pdf", folder_id=folder.id, description="Tax return")
    ready(repository, db, "r.pdf", tags=["tax"])
    ready(repository, db, "other.txt")
    ready(repository, db, "tax.txt", owner="user-2")

    found = repository.list("user-1", q="TAX")

    assert sorted(names(found)) == ["Tax 2026.pdf", "r.pdf", "scan.pdf"]


def test_search_treats_like_wildcards_literally(repository, db):
    for name in ("100%.txt", "1000.txt", "a_b.txt", "axb.txt"):
        ready(repository, db, name)

    assert names(repository.list("user-1", q="100%")) == ["100%.txt"]
    assert names(repository.list("user-1", q="a_b")) == ["a_b.txt"]


def test_tag_filter_is_containment_and_combines_with_q(repository, db):
    ready(repository, db, "a.txt", tags=["tax", "2026"])
    ready(repository, db, "b.txt", tags=["taxes"])
    ready(repository, db, "c.txt", tags=["tax"], description="receipt")

    assert sorted(names(repository.list("user-1", tag="tax"))) == ["a.txt", "c.txt"]
    assert names(repository.list("user-1", tag="tax", q="receipt")) == ["c.txt"]


def test_sort_is_case_insensitive_and_paging_is_stable_on_ties(repository, db):
    for name in ("b.txt", "A.txt", "c.txt", "d.txt"):
        ready(repository, db, name)
    db.execute(text("UPDATE files SET updated_at = '2026-01-01T00:00:00+00:00'"))
    db.commit()

    by_name = repository.list("user-1", sort="name", order="asc")
    first = repository.list("user-1", limit=2, offset=0)
    second = repository.list("user-1", limit=2, offset=2)

    assert names(by_name) == ["A.txt", "b.txt", "c.txt", "d.txt"]
    assert len({row.id for row in [*first, *second]}) == 4
