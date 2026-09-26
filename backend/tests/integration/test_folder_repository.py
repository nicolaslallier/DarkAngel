"""Integration — the folder repository's SQL, against a real PostgreSQL."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.repositories.files import FileRepository, NameTaken
from app.repositories.folders import FolderRepository

OLD = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture
def folders(db):
    return FolderRepository(db)


@pytest.fixture
def files(db):
    return FileRepository(db)


def ready_file(files, name="a.txt", folder_id=None, owner="user-1"):
    row = files.reserve(
        owner,
        name=name,
        folder_id=folder_id,
        size_bytes=1,
        content_type="text/plain",
        quota_bytes=10**9,
    )
    return files.finalize(row, s3_version_id="v", actor_sub=owner)


def test_list_returns_the_owners_live_folders_by_name(folders):
    folders.create("user-1", name="b", parent_id=None)
    folders.create("user-1", name="A", parent_id=None)
    folders.create("user-2", name="theirs", parent_id=None)

    assert [f.name for f in folders.list("user-1")] == ["A", "b"]


def test_a_root_sibling_clash_is_refused_by_the_index_whatever_the_case(folders):
    folders.create("user-1", name="work", parent_id=None)

    with pytest.raises(NameTaken):
        folders.create("user-1", name="WORK", parent_id=None)

    # The session survived the rollback and is usable again.
    folders.create("user-1", name="home", parent_id=None)
    assert [f.name for f in folders.list("user-1")] == ["home", "work"]


def test_the_same_name_is_fine_under_another_parent_or_owner(folders):
    parent = folders.create("user-1", name="A", parent_id=None)
    folders.create("user-1", name="work", parent_id=None)

    folders.create("user-1", name="work", parent_id=parent.id)
    folders.create("user-2", name="work", parent_id=None)


def test_a_trashed_folder_frees_its_name(folders):
    first = folders.create("user-1", name="work", parent_id=None)
    folders.soft_delete_subtree("user-1", first.id)

    second = folders.create("user-1", name="work", parent_id=None)

    assert folders.get("user-1", second.id) is not None
    assert folders.get("user-1", first.id) is None


def test_rename_into_a_clash_raises_and_keeps_the_old_name(folders):
    folders.create("user-1", name="a", parent_id=None)
    b = folders.create("user-1", name="b", parent_id=None)

    with pytest.raises(NameTaken):
        folders.update(b, name="A")

    assert folders.get("user-1", b.id).name == "b"


def test_a_case_only_rename_is_not_a_clash_with_itself(folders):
    row = folders.create("user-1", name="work", parent_id=None)

    folders.update(row, name="Work")

    assert folders.get("user-1", row.id).name == "Work"


def test_is_cycle_sees_the_folder_itself_and_every_descendant(folders):
    a = folders.create("user-1", name="A", parent_id=None)
    b = folders.create("user-1", name="B", parent_id=a.id)
    c = folders.create("user-1", name="C", parent_id=b.id)
    other = folders.create("user-1", name="Other", parent_id=None)

    assert folders.is_cycle("user-1", a.id, a.id)
    assert folders.is_cycle("user-1", a.id, c.id)
    assert not folders.is_cycle("user-1", c.id, a.id)
    assert not folders.is_cycle("user-1", b.id, other.id)


def test_subtree_counts_only_live_descendants(folders, files):
    a = folders.create("user-1", name="A", parent_id=None)
    b = folders.create("user-1", name="B", parent_id=a.id)
    ready_file(files, "one.txt", a.id)
    ready_file(files, "two.txt", b.id)
    files.soft_delete(ready_file(files, "gone.txt", b.id))

    assert folders.subtree_counts("user-1", a.id) == (1, 2)
    assert folders.subtree_counts("user-1", b.id) == (0, 1)


def test_soft_delete_subtree_shares_one_stamp_and_spares_pre_trashed_rows(folders, files, db):
    a = folders.create("user-1", name="A", parent_id=None)
    b = folders.create("user-1", name="B", parent_id=a.id)
    keep = folders.create("user-1", name="Keep", parent_id=None)
    ready_file(files, "one.txt", a.id)
    ready_file(files, "two.txt", b.id)
    old = ready_file(files, "old.txt", b.id)
    db.execute(text("UPDATE files SET deleted_at = :at WHERE id = :id"), {"at": OLD, "id": old.id})
    db.commit()

    assert folders.soft_delete_subtree("user-1", a.id) == (1, 2)

    stamps = db.execute(
        text(
            "SELECT deleted_at FROM folders WHERE id IN (:a, :b) "
            "UNION ALL SELECT deleted_at FROM files WHERE name IN ('one.txt', 'two.txt')"
        ),
        {"a": a.id, "b": b.id},
    ).scalars()
    assert len(set(stamps)) == 1
    old_stamp = db.execute(text("SELECT deleted_at FROM files WHERE id = :id"), {"id": old.id})
    assert old_stamp.scalar_one() == OLD
    assert [f.name for f in folders.list("user-1")] == ["Keep"]
    assert folders.get("user-1", keep.id) is not None


def test_folder_queries_never_cross_owners(folders, files):
    a = folders.create("user-1", name="A", parent_id=None)
    ready_file(files, "one.txt", a.id)

    assert folders.get("user-2", a.id) is None
    assert folders.list("user-2") == []
    assert folders.subtree_counts("user-2", a.id) == (0, 0)
    assert folders.soft_delete_subtree("user-2", a.id) == (0, 0)
    assert folders.get("user-1", a.id) is not None


def test_get_of_an_unknown_id_is_none(folders):
    assert folders.get("user-1", uuid.uuid4()) is None
