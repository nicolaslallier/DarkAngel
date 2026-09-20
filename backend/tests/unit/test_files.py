from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


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


def test_users_only_see_their_own_files(store):
    upload("secret.txt", sub="user-1")

    assert client.get("/api/files", headers=auth("user-2")).json() == []
    assert client.get("/api/files/secret.txt", headers=auth("user-2")).status_code == 404


def test_files_need_a_token():
    assert client.get("/api/files").status_code == 401
    assert client.post("/api/files", files={"file": ("a.txt", b"x")}).status_code == 401
    assert client.get("/api/files/a.txt").status_code == 401
    assert client.delete("/api/files/a.txt").status_code == 401
