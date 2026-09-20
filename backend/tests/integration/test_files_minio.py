import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)

# `put_object` is called with part_size=10 MiB and length=-1, so anything past
# that goes up as a real multipart upload.
PART_SIZE = 10 * 1024 * 1024


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def upload(name, data=b"hello", sub="user-1", content_type="text/plain"):
    return client.post("/api/files", headers=auth(sub), files={"file": (name, data, content_type)})


def test_upload_list_download_delete_round_trip():
    assert upload("notes.txt", b"hello").status_code == 204

    listed = client.get("/api/files", headers=auth()).json()
    assert [f["name"] for f in listed] == ["notes.txt"]
    assert listed[0]["size"] == 5
    # Real S3 returns a real timestamp; the FakeMinio double returns None.
    assert listed[0]["modified"] is not None

    got = client.get("/api/files/notes.txt", headers=auth())
    assert got.status_code == 200
    assert got.content == b"hello"
    # Starlette appends "; charset=utf-8" to any text/* media type unless
    # Content-Type is already in the response headers (init_headers); the
    # download route relies on media_type, so this is what it actually sends.
    assert got.headers["content-type"] == "text/plain; charset=utf-8"
    assert got.headers["content-length"] == "5"

    assert client.delete("/api/files/notes.txt", headers=auth()).status_code == 204
    assert client.get("/api/files", headers=auth()).json() == []


def test_users_cannot_reach_each_others_files():
    assert upload("secret.txt", sub="user-1").status_code == 204

    assert client.get("/api/files", headers=auth("user-2")).json() == []
    assert client.get("/api/files/secret.txt", headers=auth("user-2")).status_code == 404

    client.delete("/api/files/secret.txt", headers=auth("user-1"))


@pytest.mark.parametrize("name", ["bail été.txt", "two words.txt", "日本語.txt"])
def test_names_with_unicode_and_spaces_round_trip(name):
    assert upload(name, b"hello").status_code == 204

    assert name in [f["name"] for f in client.get("/api/files", headers=auth()).json()]
    assert client.get(f"/api/files/{name}", headers=auth()).content == b"hello"

    assert client.delete(f"/api/files/{name}", headers=auth()).status_code == 204


def test_a_multipart_sized_upload_survives_the_round_trip():
    payload = b"x" * (PART_SIZE + 1024)

    assert upload("big.bin", payload, content_type="application/octet-stream").status_code == 204

    got = client.get("/api/files/big.bin", headers=auth())
    assert len(got.content) == len(payload)
    assert got.content == payload

    client.delete("/api/files/big.bin", headers=auth())


def test_reuploading_a_name_replaces_the_object():
    assert upload("dup.txt", b"first").status_code == 204
    assert upload("dup.txt", b"second-and-longer").status_code == 204

    listed = [f for f in client.get("/api/files", headers=auth()).json() if f["name"] == "dup.txt"]
    assert len(listed) == 1
    assert client.get("/api/files/dup.txt", headers=auth()).content == b"second-and-longer"

    client.delete("/api/files/dup.txt", headers=auth())


def test_downloading_a_missing_file_is_404():
    assert client.get("/api/files/nope.txt", headers=auth()).status_code == 404


def test_deleting_a_missing_file_succeeds():
    # S3 DELETE is idempotent: MinIO reports success for a key that is not there.
    assert client.delete("/api/files/nope.txt", headers=auth()).status_code == 204


def test_unauthenticated_requests_never_reach_minio():
    assert client.get("/api/files").status_code == 401
    assert client.post("/api/files", files={"file": ("a.txt", b"x")}).status_code == 401
    assert client.get("/api/files/a.txt").status_code == 401
    assert client.delete("/api/files/a.txt").status_code == 401
