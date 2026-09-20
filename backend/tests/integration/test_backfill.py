"""Integration — the one-shot migration from <sub>/<name> keys to <sub>/<uuid>."""

import io

from app.api.routes.files import minio_client
from app.models.files import File, FileVersion
from app.scripts.backfill import backfill


def put(bucket, key, data=b"hello"):
    minio_client().put_object(bucket, key, io.BytesIO(data), length=len(data))


def test_a_legacy_object_gets_a_row_and_a_uuid_key(db, minio_bucket):
    put(minio_bucket, "user-1/bail été.txt")

    moved, skipped = backfill(confirm=True)

    assert len(moved) == 1 and skipped == []
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

    assert backfill(confirm=True) == ([], [])
    assert db.query(File).count() == 1


def test_dry_run_writes_nothing(db, minio_bucket):
    put(minio_bucket, "user-1/a.txt")

    # The bucket is session-scoped, so by the time this test runs,
    # test_files_minio.py has legitimately left objects in here -- the proof
    # this test owes is that its OWN dry run added and removed nothing, not
    # that the bucket holds only what this test put in.
    before = {o.object_name for o in minio_client().list_objects(minio_bucket, recursive=True)}

    moved, skipped = backfill(confirm=True, dry_run=True)

    assert len(moved) == 1 and skipped == []
    assert db.query(File).count() == 0

    after = {o.object_name for o in minio_client().list_objects(minio_bucket, recursive=True)}
    assert after == before


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


def test_a_case_collision_is_reported_rather_than_fatal(db, minio_bucket):
    """uq_files_folder_name keys on lower(name), but the old API never
    case-folded, so both of these are legitimate legacy objects. The run must
    finish, and must not leave the loser copied under a UUID key -- an orphan
    there would be copied again by every later run."""
    put(minio_bucket, "user-3/A.txt")
    put(minio_bucket, "user-3/a.txt")

    moved, skipped = backfill(confirm=True)

    mine = [(old, new) for old, new in moved if old.startswith("user-3/")]
    assert len(mine) == 1
    assert skipped == [key for key in ("user-3/A.txt", "user-3/a.txt") if key != mine[0][0]]
    assert db.query(File).filter_by(owner_sub="user-3").count() == 1

    keys = {o.object_name for o in minio_client().list_objects(minio_bucket, recursive=True)}
    # The migrated object under its new key, the skipped one still under its
    # old one, and nothing else: no orphaned UUID-keyed copy.
    assert {k for k in keys if k.startswith("user-3/")} == {mine[0][1], skipped[0]}
