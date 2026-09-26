"""Unit — the folder routes, against FakeFolderRepository (`repo.folders`)."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def create(name="Invoices", parent_id=None, sub="user-1"):
    return client.post(
        "/api/folders", headers=auth(sub), json={"name": name, "parent_id": parent_id}
    )


def patch(folder_id, body, sub="user-1"):
    return client.patch(f"/api/folders/{folder_id}", headers=auth(sub), json=body)


def listed(sub="user-1"):
    return client.get("/api/folders", headers=auth(sub)).json()


def seed_file(repo, folder_id, name="a.txt", sub="user-1"):
    row = repo.reserve(
        sub,
        name=name,
        folder_id=uuid.UUID(folder_id),
        size_bytes=1,
        content_type="text/plain",
        quota_bytes=10**9,
    )
    return repo.finalize(row, s3_version_id="v1", actor_sub=sub)


def test_create_returns_201_with_the_folder_and_audits_it(repo):
    response = create()

    assert response.status_code == 201
    body = response.json()
    assert (body["name"], body["parent_id"]) == ("Invoices", None)
    actor, action, target_type, target_id, detail = repo.audits[-1]
    assert (actor, action, target_type) == ("user-1", "folder_create", "folder")
    assert str(target_id) == body["id"]
    assert detail == {"name": "Invoices", "parent_id": None}


def test_create_trims_and_validates_the_name(repo):
    assert create("  Bills  ").json()["name"] == "Bills"
    assert create("   ").status_code == 422
    assert create("..").status_code == 422


def test_a_sibling_clash_is_a_409_whatever_the_case(repo):
    create("work")

    response = create("WORK")

    assert response.status_code == 409
    assert response.json()["detail"] == "A folder named WORK already exists here"


def test_the_same_name_is_fine_under_another_parent(repo):
    parent = create("A").json()["id"]
    create("work")

    assert create("work", parent_id=parent).status_code == 201


def test_create_under_an_unknown_parent_is_a_404(repo):
    assert create(parent_id=str(uuid.uuid4())).status_code == 404


def test_list_returns_only_the_callers_live_folders(repo):
    mine = create("Mine").json()
    create("Theirs", sub="user-2")

    assert listed() == [mine]


def test_rename_is_audited_with_before_and_after(repo):
    folder_id = create("Invoices").json()["id"]

    response = patch(folder_id, {"name": "Bills"})

    assert response.status_code == 200
    assert response.json()["name"] == "Bills"
    assert repo.audits[-1][1:] == (
        "folder_rename",
        "folder",
        uuid.UUID(folder_id),
        {"before": "Invoices", "after": "Bills"},
    )


def test_rename_into_a_sibling_name_is_a_409(repo):
    create("A")
    folder_id = create("B").json()["id"]

    assert patch(folder_id, {"name": "a"}).status_code == 409
    assert sorted(f["name"] for f in listed()) == ["A", "B"]


def test_a_case_only_rename_is_not_a_clash_with_itself(repo):
    folder_id = create("work").json()["id"]

    response = patch(folder_id, {"name": "Work"})

    assert response.status_code == 200
    assert response.json()["name"] == "Work"


def test_omitted_parent_is_unchanged_and_explicit_null_is_the_root(repo):
    parent = create("A").json()["id"]
    folder_id = create("B", parent_id=parent).json()["id"]

    assert patch(folder_id, {"name": "C"}).json()["parent_id"] == parent

    moved = patch(folder_id, {"parent_id": None})

    assert moved.json()["parent_id"] is None
    assert repo.audits[-1][1] == "folder_move"
    assert repo.audits[-1][4] == {"before": parent, "after": None}


def test_an_empty_or_no_op_patch_is_200_and_audits_nothing(repo):
    parent = create("A").json()["id"]
    folder_id = create("B", parent_id=parent).json()["id"]
    audits = len(repo.audits)

    assert patch(folder_id, {}).status_code == 200
    assert patch(folder_id, {"parent_id": parent, "name": "B"}).status_code == 200
    assert len(repo.audits) == audits


def test_a_folder_cannot_move_into_itself_or_below_itself(repo):
    a = create("A").json()["id"]
    b = create("B", parent_id=a).json()["id"]

    assert patch(a, {"parent_id": b}).status_code == 409
    assert patch(a, {"parent_id": a}).status_code == 409
    assert next(f for f in listed() if f["id"] == a)["parent_id"] is None


def test_move_under_an_unknown_parent_is_a_404(repo):
    folder_id = create().json()["id"]

    assert patch(folder_id, {"parent_id": str(uuid.uuid4())}).status_code == 404


def test_delete_an_empty_folder(repo):
    folder_id = create("A").json()["id"]

    assert client.delete(f"/api/folders/{folder_id}", headers=auth()).status_code == 204
    assert listed() == []
    assert repo.audits[-1][1] == "folder_delete"
    assert repo.audits[-1][4] == {"name": "A", "folders": 0, "files": 0}


def test_a_non_empty_folder_needs_recursive(repo):
    a = create("A").json()["id"]
    b = create("B", parent_id=a).json()["id"]
    seed_file(repo, b)

    response = client.delete(f"/api/folders/{a}", headers=auth())

    assert response.status_code == 409
    assert response.json() == {
        "detail": "A is not empty (1 folders, 1 files)",
        "folders": 1,
        "files": 1,
    }
    assert len(listed()) == 2


def test_recursive_delete_trashes_the_whole_subtree(repo):
    a = create("A").json()["id"]
    b = create("B", parent_id=a).json()["id"]
    inside = seed_file(repo, b)

    response = client.delete(f"/api/folders/{a}?recursive=true", headers=auth())

    assert response.status_code == 204
    assert listed() == []
    assert inside.deleted_at is not None
    assert repo.audits[-1][4] == {"name": "A", "folders": 1, "files": 1}


def test_unknown_or_foreign_folders_are_404(repo):
    folder_id = create().json()["id"]
    unknown = uuid.uuid4()

    assert patch(folder_id, {"name": "x"}, sub="user-2").status_code == 404
    assert patch(unknown, {"name": "x"}).status_code == 404
    assert client.delete(f"/api/folders/{folder_id}", headers=auth("user-2")).status_code == 404
    assert client.delete(f"/api/folders/{unknown}", headers=auth()).status_code == 404


def test_the_folder_routes_need_a_token(repo):
    assert client.get("/api/folders").status_code == 401
    assert client.post("/api/folders", json={"name": "A"}).status_code == 401
