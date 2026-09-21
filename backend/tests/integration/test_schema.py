"""Integration — the schema, against a real PostgreSQL.

Everything here needs SQL that SQLite cannot run (uuid, text[], jsonb, partial
indexes with NULLS NOT DISTINCT), which is why none of it is a unit test.
"""

import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.core.db import engine
from app.models.files import AuditLog, File, FileVersion, Folder


def test_the_database_is_reachable(db):
    assert db.scalar(text("SELECT 1")) == 1


def test_it_is_postgres_15_or_newer(db):
    # `UNIQUE NULLS NOT DISTINCT` in migration 0001 needs 15+.
    major = db.scalar(text("SHOW server_version_num"))
    assert int(major) >= 150000


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
