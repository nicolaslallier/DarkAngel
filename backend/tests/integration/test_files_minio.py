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
    from app.api.routes.files import minio_client
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "user_quota_bytes", 3)

    # The bucket is session-scoped and soft-delete never touches MinIO, so
    # earlier tests' objects are still in here -- the proof this test owes is
    # that its OWN refused upload added nothing, not that the bucket is empty.
    before = {o.object_name for o in minio_client().list_objects(minio_bucket, recursive=True)}

    assert upload("too-big.txt", b"hello").status_code == 413
    assert db.query(File).count() == 0

    after = {o.object_name for o in minio_client().list_objects(minio_bucket, recursive=True)}
    assert after == before
