import uuid
from datetime import datetime

from fastapi.testclient import TestClient

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
