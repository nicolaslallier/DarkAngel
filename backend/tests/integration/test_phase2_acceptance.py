"""Acceptance — the five Phase 2 scenarios of docs/files-feature.md §12, one
test each, through the real routes, a real PostgreSQL and a real MinIO."""

from fastapi.testclient import TestClient

from app.api.routes.files import minio_client
from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def folder(name, parent_id=None, sub="user-1"):
    response = client.post(
        "/api/folders", headers=auth(sub), json={"name": name, "parent_id": parent_id}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(name, folder_id=None, sub="user-1"):
    response = client.post(
        "/api/files",
        headers=auth(sub),
        data={"folder_id": folder_id} if folder_id else {},
        files={"file": (name, b"hello", "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _object_versions(bucket):
    """A snapshot of every object version and delete marker in the bucket,
    as (key, version_id) pairs -- proof positive that a MinIO read or write
    happened, rather than trusting a mock that the route never calls."""
    client_ = minio_client()
    return {
        (obj.object_name, obj.version_id)
        for obj in client_.list_objects(bucket, recursive=True, include_version=True)
    }


def test_renaming_a_folder_reads_and_writes_no_object(db, minio_bucket):
    # Given a folder "Invoices" containing a file
    invoices = folder("Invoices")
    upload("march.pdf", invoices)
    before = _object_versions(minio_bucket)

    # When the user renames the folder to "Bills"
    response = client.patch(f"/api/folders/{invoices}", headers=auth(), json={"name": "Bills"})

    # Then the response is 200 and no MinIO object has been read or written
    assert response.status_code == 200
    assert response.json()["name"] == "Bills"
    assert _object_versions(minio_bucket) == before


def test_moving_a_folder_into_its_child_is_refused(db, minio_bucket):
    # Given a folder tree A > B
    a = folder("A")
    b = folder("B", parent_id=a)
    before = client.get("/api/folders", headers=auth()).json()

    # When the user moves A into B
    response = client.patch(f"/api/folders/{a}", headers=auth(), json={"parent_id": b})

    # Then the response is 409 and the tree is unchanged
    assert response.status_code == 409
    assert client.get("/api/folders", headers=auth()).json() == before


def test_a_second_root_folder_named_work_is_refused(db, minio_bucket):
    # Given a root folder named "work" exists
    folder("work")

    # When the user creates another root folder named "work"
    response = client.post("/api/folders", headers=auth(), json={"name": "work"})

    # Then the response is 409
    assert response.status_code == 409


def test_search_finds_a_tag_and_a_description_and_no_one_elses_file(db, minio_bucket):
    # Given a file tagged "tax" and another described "tax return"
    tagged = upload("a.txt")
    described = upload("b.txt")
    upload("c.txt")
    upload("tax.txt", sub="user-2")
    client.patch(f"/api/files/{tagged}", headers=auth(), json={"tags": ["tax"]})
    client.patch(f"/api/files/{described}", headers=auth(), json={"description": "tax return"})

    # When the user searches "TAX"
    found = client.get("/api/files", headers=auth(), params={"q": "TAX"}).json()

    # Then both are returned, and no other user's files are
    assert sorted(f["id"] for f in found) == sorted([tagged, described])


def test_deleting_a_non_empty_folder_names_the_child_count(db, minio_bucket):
    # Given a folder containing two files
    box = folder("Box")
    upload("one.txt", box)
    upload("two.txt", box)

    # When the user deletes it without ?recursive=true
    response = client.delete(f"/api/folders/{box}", headers=auth())

    # Then the response is 409 naming the child count
    assert response.status_code == 409
    assert (response.json()["folders"], response.json()["files"]) == (0, 2)
