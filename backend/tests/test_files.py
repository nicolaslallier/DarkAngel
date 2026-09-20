from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from minio.error import S3Error

from app.api.routes import files
from app.main import app
from tests.conftest import token

client = TestClient(app)


class FakeMinio:
    """The slice of minio.Minio the files routes use, over a dict."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_object(self, _bucket, key, data, length, part_size, content_type):
        self.objects[key] = (data.read(), content_type)

    def list_objects(self, _bucket, prefix):
        return [
            SimpleNamespace(object_name=k, size=len(v[0]), last_modified=None)
            for k, v in self.objects.items()
            if k.startswith(prefix)
        ]

    def get_object(self, _bucket, key):
        if key not in self.objects:
            raise S3Error(None, "NoSuchKey", "missing", key, "", "")
        data, content_type = self.objects[key]
        return SimpleNamespace(
            headers={"Content-Type": content_type, "Content-Length": str(len(data))},
            stream=lambda _size: iter([data]),
            close=lambda: None,
            release_conn=lambda: None,
        )

    def remove_object(self, _bucket, key):
        self.objects.pop(key, None)


@pytest.fixture(autouse=True)
def store(monkeypatch):
    fake = FakeMinio()
    monkeypatch.setattr(files, "minio_client", lambda: fake)
    return fake


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def upload(name, data=b"hello", sub="user-1"):
    return client.post("/api/files", headers=auth(sub), files={"file": (name, data, "text/plain")})


def test_upload_list_download_delete(store):
    assert upload("bail été.txt").status_code == 204
    assert store.objects["user-1/bail été.txt"] == (b"hello", "text/plain")

    listed = client.get("/api/files", headers=auth()).json()
    assert listed == [{"name": "bail été.txt", "size": 5, "modified": None}]

    got = client.get("/api/files/bail été.txt", headers=auth())
    assert got.status_code == 200
    assert got.content == b"hello"
    disposition = "attachment; filename*=UTF-8''bail%20%C3%A9t%C3%A9.txt"
    assert got.headers["content-disposition"] == disposition

    assert client.delete("/api/files/bail été.txt", headers=auth()).status_code == 204
    assert store.objects == {}


def test_users_only_see_their_own_files():
    upload("secret.txt", sub="user-1")

    assert client.get("/api/files", headers=auth("user-2")).json() == []
    assert client.get("/api/files/secret.txt", headers=auth("user-2")).status_code == 404


@pytest.mark.parametrize("name", ["..", "a\\b", "x" * 256])
def test_rejects_unsafe_names(name, store):
    assert upload(name).status_code == 422
    assert store.objects == {}


def test_files_need_a_token():
    assert client.get("/api/files").status_code == 401
    assert client.post("/api/files", files={"file": ("a.txt", b"x")}).status_code == 401
