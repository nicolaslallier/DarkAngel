import uuid
from datetime import datetime

from fastapi.testclient import TestClient
from minio.error import S3Error

from app.core.config import get_settings
from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def seed(repo, name="a.txt", sub="user-1", size=5, content_type="text/plain"):
    """A ready file straight in the fake, so read tests do not depend on upload."""
    row = repo.reserve(
        sub,
        name=name,
        folder_id=None,
        size_bytes=size,
        content_type=content_type,
        quota_bytes=10**9,
    )
    repo.finalize(row, s3_version_id="v1", actor_sub=sub)
    return row


def test_list_returns_the_owners_ready_files(repo):
    row = seed(repo, name="bail été.txt")

    listed = client.get("/api/files", headers=auth()).json()

    # Pydantic v2 serializes an aware UTC datetime with a "Z" suffix, not the
    # "+00:00" that `datetime.isoformat()` produces -- both are valid ISO 8601,
    # so compare the parsed values rather than the raw strings.
    assert len(listed) == 1
    modified = datetime.fromisoformat(listed[0].pop("modified"))
    assert modified == row.updated_at
    assert listed == [
        {
            "id": str(row.id),
            "name": "bail été.txt",
            "size": 5,
            "content_type": "text/plain",
        }
    ]


def test_list_excludes_other_owners(repo):
    seed(repo, name="secret.txt", sub="user-1")

    assert client.get("/api/files", headers=auth("user-2")).json() == []


def test_list_excludes_pending_uploads(repo):
    repo.reserve(
        "user-1",
        name="half.txt",
        folder_id=None,
        size_bytes=5,
        content_type="text/plain",
        quota_bytes=10**9,
    )

    assert client.get("/api/files", headers=auth()).json() == []


def test_get_returns_one_file(repo):
    row = seed(repo)

    response = client.get(f"/api/files/{row.id}", headers=auth())

    assert response.status_code == 200
    assert response.json()["id"] == str(row.id)


def test_get_hides_another_owners_file_behind_404(repo):
    row = seed(repo, sub="user-1")

    # 404, not 403: a 403 would confirm the id exists.
    assert client.get(f"/api/files/{row.id}", headers=auth("user-2")).status_code == 404


def test_get_rejects_a_malformed_id(repo):
    assert client.get("/api/files/not-a-uuid", headers=auth()).status_code == 422


def test_get_returns_404_for_an_unknown_id(repo):
    assert client.get(f"/api/files/{uuid.uuid4()}", headers=auth()).status_code == 404


def test_the_routes_need_a_token(repo):
    row = seed(repo)

    assert client.get("/api/files").status_code == 401
    assert client.get(f"/api/files/{row.id}").status_code == 401


def test_listing_sweeps_abandoned_reservations(repo, store):
    store.objects["user-1/orphan"] = (b"x", "text/plain")
    repo.swept = ["user-1/orphan"]

    client.get("/api/files", headers=auth())

    assert store.objects == {}


def upload(name="a.txt", data=b"hello", sub="user-1", content_type="text/plain"):
    return client.post("/api/files", headers=auth(sub), files={"file": (name, data, content_type)})


def test_upload_stores_the_bytes_under_a_uuid_key(repo, store):
    response = upload("bail été.txt")

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "bail été.txt"
    assert store.objects[f"user-1/{body['id']}"] == (b"hello", "text/plain")


def test_upload_makes_the_file_listable(repo, store):
    created = upload().json()

    assert [f["id"] for f in client.get("/api/files", headers=auth()).json()] == [created["id"]]


def test_upload_writes_an_audit_row(repo, store):
    created = upload().json()

    actor, action, target_type, target_id, detail = repo.audits[-1]
    assert (actor, action, target_type) == ("user-1", "upload", "file")
    assert str(target_id) == created["id"]
    assert detail == {"name": "a.txt", "size": 5}


def test_upload_refuses_a_file_over_the_size_limit(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 4, raising=False)

    response = upload(data=b"too long")

    assert response.status_code == 413
    assert store.objects == {}
    assert repo.rows == []


def test_upload_refuses_a_file_over_the_quota(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "user_quota_bytes", 6, raising=False)
    upload(name="first.txt")

    response = upload(name="second.txt")

    assert response.status_code == 413
    assert "bytes used" in response.json()["detail"]


def test_upload_refuses_a_denied_extension(repo, store):
    response = upload(name="payload.svg", content_type="image/svg+xml")

    assert response.status_code == 415
    assert store.objects == {}


def test_a_failed_upload_leaves_no_reservation(repo, store, monkeypatch):
    def explode(*_args, **_kwargs):
        raise S3Error(None, "InternalError", "boom", "k", "", "")

    monkeypatch.setattr(store, "put_object", explode)

    response = upload()

    assert response.status_code == 502
    assert repo.rows == []
    assert client.get("/api/files", headers=auth()).json() == []


def test_upload_needs_a_token(repo):
    assert (
        client.post("/api/files", files={"file": ("a.txt", b"x", "text/plain")}).status_code == 401
    )


def test_a_same_name_upload_versions_the_existing_file(repo, store):
    first = upload(name="notes.txt", data=b"one").json()

    second = client.post(
        "/api/files",
        headers=auth(),
        files={"file": ("notes.txt", b"two now", "text/plain")},
    )

    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
    assert second.json()["size"] == 7
    assert repo.versions[uuid.UUID(first["id"])] == 2
    assert len(repo.rows) == 1


def test_the_new_version_replaces_the_bytes_at_the_same_key(repo, store):
    created = upload(name="notes.txt", data=b"one").json()

    client.post("/api/files", headers=auth(), files={"file": ("notes.txt", b"two", "text/plain")})

    assert store.objects[f"user-1/{created['id']}"] == (b"two", "text/plain")


def test_a_same_name_upload_by_another_owner_is_a_new_file(repo, store):
    first = upload(name="notes.txt", sub="user-1").json()

    second = client.post(
        "/api/files", headers=auth("user-2"), files={"file": ("notes.txt", b"x", "text/plain")}
    )

    assert second.status_code == 201
    assert second.json()["id"] != first["id"]


def test_a_versioning_upload_still_respects_the_quota(repo, store, monkeypatch):
    monkeypatch.setattr(get_settings(), "user_quota_bytes", 10, raising=False)
    upload(name="notes.txt", data=b"abc")

    response = client.post(
        "/api/files", headers=auth(), files={"file": ("notes.txt", b"x" * 20, "text/plain")}
    )

    assert response.status_code == 413
