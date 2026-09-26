"""Regression — spec risk R-1, ownership isolation, ROUTE layer only.

Before the metadata moved to Postgres, isolation was structural: a user's
objects lived under a MinIO key prefixed with their `sub`, so there was no
query to get wrong. Now every route reaches a row through `FileRepository`,
and isolation is only as good as `WHERE owner_sub = ...` in that one module.

This file runs against `FakeFileRepository` (the `repo` fixture), an
in-memory stand-in that filters by `owner_sub` in Python. It does NOT
exercise the real SQL `WHERE owner_sub = ...` clause -- a regression that
dropped that clause from `FileRepository` itself would NOT be caught here,
because the fake has its own, separately-written filter. What this file
does pin is the ROUTE layer: every file route passes the CALLER's
`claims["sub"]` through to the repository rather than, say, a value taken
from the request body or path, and cross-owner access comes back as 404,
never 403 (a 403 would confirm the id exists).

The SQL-layer half of R-1 -- that `FileRepository`'s queries themselves
never cross owners -- is pinned separately, against real PostgreSQL, by
`tests/integration/test_repository.py::test_list_and_get_never_cross_owners`
and `::test_find_by_name_never_crosses_owners`. `docs/files-feature.md`
names a standing regression test as one of R-1's two mitigations (the other
being the single repository module); it takes both this file and those two
integration tests together to cover it end to end -- neither layer alone is
sufficient, and this file does not close R-1 by itself.

Covers, for user-2 against user-1's file, at the route layer:
- list does not include it
- get by id is a 404 (not a 403)
- content by id is a 404
- delete by id is a 404, and the file is still listed for user-1 afterwards
- a same-name upload creates a separate file rather than versioning it
- folders: list, rename/move, delete (plain and recursive), and listing files
  in a foreign folder are all 404 or empty, and nothing changes
- a foreign folder cannot be a parent, a move target or an upload target
- PATCH on a foreign file is a 404 and changes nothing
- search (q and tag) never returns a foreign file
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


def folder(name="Invoices", parent_id=None, sub="user-1"):
    return client.post(
        "/api/folders", headers=auth(sub), json={"name": name, "parent_id": parent_id}
    )


def test_user_two_cannot_see_or_touch_user_ones_folder(repo, store):
    created = folder().json()
    folder_id = created["id"]

    assert client.get("/api/folders", headers=auth("user-2")).json() == []
    rename = client.patch(f"/api/folders/{folder_id}", headers=auth("user-2"), json={"name": "x"})
    assert rename.status_code == 404
    for query in ("", "?recursive=true"):
        response = client.delete(f"/api/folders/{folder_id}{query}", headers=auth("user-2"))
        assert response.status_code == 404
    listing = client.get("/api/files", headers=auth("user-2"), params={"folder_id": folder_id})
    assert listing.status_code == 404
    assert client.get("/api/folders", headers=auth("user-1")).json() == [created]


def test_user_two_cannot_use_user_ones_folder_as_a_target(repo, store):
    target = folder().json()["id"]
    own_folder = folder(name="Mine", sub="user-2").json()["id"]
    own_file = upload(sub="user-2").json()["id"]

    assert folder(name="x", parent_id=target, sub="user-2").status_code == 404
    move_folder = client.patch(
        f"/api/folders/{own_folder}", headers=auth("user-2"), json={"parent_id": target}
    )
    assert move_folder.status_code == 404
    move_file = client.patch(
        f"/api/files/{own_file}", headers=auth("user-2"), json={"folder_id": target}
    )
    assert move_file.status_code == 404
    into = client.post(
        "/api/files",
        headers=auth("user-2"),
        data={"folder_id": target},
        files={"file": ("b.txt", b"x", "text/plain")},
    )
    assert into.status_code == 404
    assert {r.folder_id for r in repo.rows} == {None}
    assert [f.parent_id for f in repo.folders.rows] == [None, None]


def test_user_two_cannot_patch_user_ones_file(repo, store):
    created = upload(sub="user-1").json()

    response = client.patch(
        f"/api/files/{created['id']}",
        headers=auth("user-2"),
        json={"name": "stolen.txt", "tags": ["x"], "folder_id": None},
    )

    assert response.status_code == 404
    assert client.get(f"/api/files/{created['id']}", headers=auth()).json() == created
    assert [a[1] for a in repo.audits] == ["upload"]


def test_search_never_returns_user_ones_files(repo, store):
    created = upload(name="tax.txt", sub="user-1").json()
    client.patch(f"/api/files/{created['id']}", headers=auth(), json={"tags": ["tax"]})

    for params in ({"q": "tax"}, {"tag": "tax"}):
        assert client.get("/api/files", headers=auth("user-2"), params=params).json() == []
