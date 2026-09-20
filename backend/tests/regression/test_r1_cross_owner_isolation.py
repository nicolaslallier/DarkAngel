"""Regression — spec risk R-1, ownership isolation.

Before the metadata moved to Postgres, isolation was structural: a user's
objects lived under a MinIO key prefixed with their `sub`, so there was no
query to get wrong. Now every route reaches a row through
`FileRepository`, and isolation is only as good as `WHERE owner_sub = ...`
in that one module -- a missing clause in any query or any future route
would let one user reach another user's file.

`docs/files-feature.md` names this as the highest-consequence risk in the
spec (R-1) and says it is mitigated by the single repository module *and* a
standing cross-user regression test. This file is that test. Every
assertion below must keep passing forever: if one of them ever fails, that
is a real cross-tenant data leak, not a test to be relaxed.

Covers, for user-2 against user-1's file:
- list does not include it
- get by id is a 404 (not a 403 -- a 403 would confirm the id exists)
- content by id is a 404
- delete by id is a 404, and the file is still listed for user-1 afterwards
- a same-name upload creates a separate file rather than versioning it
"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def upload(name="a.txt", data=b"hello", sub="user-1", content_type="text/plain"):
    return client.post("/api/files", headers=auth(sub), files={"file": (name, data, content_type)})


def test_user_two_cannot_list_user_ones_file(repo, store):
    upload(sub="user-1")

    assert client.get("/api/files", headers=auth("user-2")).json() == []


def test_user_two_gets_a_404_not_a_403_for_user_ones_file(repo, store):
    created = upload(sub="user-1").json()

    response = client.get(f"/api/files/{created['id']}", headers=auth("user-2"))

    assert response.status_code == 404


def test_user_two_cannot_stream_user_ones_content(repo, store):
    created = upload(sub="user-1").json()

    response = client.get(f"/api/files/{created['id']}/content", headers=auth("user-2"))

    assert response.status_code == 404


def test_user_two_cannot_delete_user_ones_file(repo, store):
    created = upload(sub="user-1").json()

    response = client.delete(f"/api/files/{created['id']}", headers=auth("user-2"))

    assert response.status_code == 404
    listed = client.get("/api/files", headers=auth("user-1")).json()
    assert [f["id"] for f in listed] == [created["id"]]


def test_a_same_name_upload_by_user_two_is_a_separate_file(repo, store):
    first = upload(name="notes.txt", sub="user-1").json()

    second = upload(name="notes.txt", sub="user-2")

    assert second.status_code == 201
    second_id = second.json()["id"]
    assert second_id != first["id"]
    assert {r.id for r in repo.rows} == {uuid.UUID(first["id"]), uuid.UUID(second_id)}
