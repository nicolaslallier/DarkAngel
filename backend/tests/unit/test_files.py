import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from minio.error import S3Error

from app.api.routes import files as files_routes
from app.core.config import get_settings
from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def seed(
    repo,
    name="a.txt",
    sub="user-1",
    size=5,
    content_type="text/plain",
    folder_id=None,
    description=None,
    tags=(),
):
    """A ready file straight in the fake, so read tests do not depend on upload."""
    row = repo.reserve(
        sub,
        name=name,
        folder_id=folder_id,
        size_bytes=size,
        content_type=content_type,
        quota_bytes=10**9,
    )
    repo.finalize(row, s3_version_id="v1", actor_sub=sub)
    row.description = description
    row.tags = list(tags)
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
            "folder_id": None,
            "description": None,
            "tags": [],
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


def test_listing_survives_a_sweep_against_unreachable_storage(repo, store):
    """§12: GET /api/files needs no object storage, so a MinIO outage during
    the ride-along sweep must not turn it into a 500. An unreachable server
    raises urllib3's MaxRetryError, which is not an S3Error."""

    def unreachable(_bucket, _key):
        raise RuntimeError("Max retries exceeded")

    seed(repo)
    repo.swept = ["user-1/orphan"]
    store.remove_object = unreachable

    response = client.get("/api/files", headers=auth())

    assert response.status_code == 200
    assert len(response.json()) == 1


def names(response):
    return [f["name"] for f in response.json()]


def test_list_shows_the_root_by_default(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    seed(repo, name="root.txt")
    seed(repo, name="inside.txt", folder_id=folder.id)

    assert names(client.get("/api/files", headers=auth())) == ["root.txt"]


def test_list_shows_one_folder_when_asked(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    seed(repo, name="root.txt")
    inside = seed(repo, name="inside.txt", folder_id=folder.id)

    response = client.get("/api/files", headers=auth(), params={"folder_id": str(folder.id)})

    assert names(response) == ["inside.txt"]
    assert response.json()[0]["folder_id"] == str(inside.folder_id)


def test_listing_an_unknown_or_foreign_folder_is_a_404(repo):
    theirs = repo.folders.create("user-2", name="A", parent_id=None)

    for folder_id in (theirs.id, uuid.uuid4()):
        response = client.get("/api/files", headers=auth(), params={"folder_id": str(folder_id)})
        assert response.status_code == 404


def test_search_ignores_the_folder_and_matches_name_description_and_tags(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    seed(repo, name="Tax 2026.pdf")
    seed(repo, name="scan.pdf", folder_id=folder.id, description="tax return")
    seed(repo, name="r.pdf", tags=["tax"])
    seed(repo, name="other.txt")

    response = client.get(
        "/api/files", headers=auth(), params={"q": "TAX", "folder_id": str(folder.id)}
    )

    assert sorted(names(response)) == ["Tax 2026.pdf", "r.pdf", "scan.pdf"]


def test_the_tag_filter_is_trimmed_lowercased_and_exact(repo):
    seed(repo, name="a.txt", tags=["tax"])
    seed(repo, name="b.txt", tags=["taxes"])

    assert names(client.get("/api/files", headers=auth(), params={"tag": " TAX "})) == ["a.txt"]


def test_q_and_tag_together_must_both_match(repo):
    seed(repo, name="a.txt", tags=["tax"])
    seed(repo, name="receipt.txt", tags=["tax"])
    seed(repo, name="receipt-2.txt")

    response = client.get("/api/files", headers=auth(), params={"q": "receipt", "tag": "tax"})

    assert names(response) == ["receipt.txt"]


def test_a_blank_search_is_the_plain_folder_listing(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    seed(repo, name="root.txt")
    seed(repo, name="inside.txt", folder_id=folder.id)

    response = client.get("/api/files", headers=auth(), params={"q": "   ", "tag": ""})

    assert response.status_code == 200
    assert names(response) == ["root.txt"]


def test_an_overlong_search_is_rejected(repo):
    assert client.get("/api/files", headers=auth(), params={"q": "x" * 201}).status_code == 422


def test_sort_by_name_ascending_ignores_case(repo):
    for name in ("b.txt", "A.txt", "c.txt"):
        seed(repo, name=name)

    response = client.get("/api/files", headers=auth(), params={"sort": "name", "order": "asc"})

    assert names(response) == ["A.txt", "b.txt", "c.txt"]


def test_sort_by_size_descending(repo):
    seed(repo, name="small.txt", size=1)
    seed(repo, name="big.txt", size=9)

    response = client.get("/api/files", headers=auth(), params={"sort": "size", "order": "desc"})

    assert names(response) == ["big.txt", "small.txt"]


def test_an_unknown_sort_key_is_rejected(repo):
    assert client.get("/api/files", headers=auth(), params={"sort": "owner"}).status_code == 422


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


def test_content_streams_the_bytes_as_an_attachment(repo, store):
    created = upload(name="bail été.txt", data=b"hello").json()

    response = client.get(f"/api/files/{created['id']}/content", headers=auth())

    assert response.status_code == 200
    assert response.content == b"hello"
    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''bail%20%C3%A9t%C3%A9.txt"
    )


def test_a_name_with_a_slash_is_fully_escaped_in_the_header(repo, store):
    # quote() leaves '/' alone by default, which would split the header value.
    created = upload(name="a/b.txt").json()

    response = client.get(f"/api/files/{created['id']}/content", headers=auth())

    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''a%2Fb.txt"


def test_an_allow_listed_type_may_be_served_inline(repo, store):
    created = upload(name="shot.png", data=b"\x89PNG", content_type="image/png").json()

    response = client.get(f"/api/files/{created['id']}/content?disposition=inline", headers=auth())

    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["content-type"].startswith("image/png")


def test_anything_else_is_forced_to_attachment(repo, store):
    created = upload(
        name="sheet.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ).json()

    response = client.get(f"/api/files/{created['id']}/content?disposition=inline", headers=auth())

    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["content-type"].startswith("application/octet-stream")


def test_every_content_response_carries_the_hardening_headers(repo, store):
    created = upload().json()

    response = client.get(f"/api/files/{created['id']}/content", headers=auth())

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"


def test_content_hides_another_owners_file_behind_404(repo, store):
    created = upload().json()

    assert (
        client.get(f"/api/files/{created['id']}/content", headers=auth("user-2")).status_code == 404
    )


def test_content_needs_a_token(repo, store):
    created = upload().json()

    assert client.get(f"/api/files/{created['id']}/content").status_code == 401


def test_delete_removes_the_file_from_the_list(repo, store):
    created = upload().json()

    assert client.delete(f"/api/files/{created['id']}", headers=auth()).status_code == 204
    assert client.get("/api/files", headers=auth()).json() == []


def test_delete_keeps_the_bytes_and_the_row(repo, store):
    created = upload().json()

    client.delete(f"/api/files/{created['id']}", headers=auth())

    assert store.objects[f"user-1/{created['id']}"] == (b"hello", "text/plain")
    assert len(repo.rows) == 1


def test_delete_writes_an_audit_row(repo, store):
    created = upload().json()

    client.delete(f"/api/files/{created['id']}", headers=auth())

    actor, action, _, target_id, detail = repo.audits[-1]
    assert (actor, action) == ("user-1", "delete")
    assert str(target_id) == created["id"]
    assert detail == {"name": "a.txt"}


def test_delete_cannot_reach_another_owners_file(repo, store):
    created = upload().json()

    assert client.delete(f"/api/files/{created['id']}", headers=auth("user-2")).status_code == 404
    assert len(client.get("/api/files", headers=auth()).json()) == 1


def test_deleting_twice_is_a_404(repo, store):
    created = upload().json()
    client.delete(f"/api/files/{created['id']}", headers=auth())

    assert client.delete(f"/api/files/{created['id']}", headers=auth()).status_code == 404


def test_the_name_is_free_again_after_a_delete(repo, store):
    first = upload(name="notes.txt").json()
    client.delete(f"/api/files/{first['id']}", headers=auth())

    second = upload(name="notes.txt")

    assert second.status_code == 201
    assert second.json()["id"] != first["id"]


def test_delete_needs_a_token(repo, store):
    created = upload().json()

    assert client.delete(f"/api/files/{created['id']}").status_code == 401


def patch(file_id, body, sub="user-1"):
    return client.patch(f"/api/files/{file_id}", headers=auth(sub), json=body)


def test_rename_is_audited_and_touches_no_object(repo, monkeypatch):
    monkeypatch.setattr(files_routes, "minio_client", lambda: pytest.fail("PATCH touched MinIO"))
    row = seed(repo, name="a.txt")

    response = patch(row.id, {"name": " b.txt "})

    assert response.status_code == 200
    assert response.json()["name"] == "b.txt"
    assert repo.audits[-1][1:] == ("rename", "file", row.id, {"before": "a.txt", "after": "b.txt"})


def test_rename_validates_the_name(repo):
    row = seed(repo)

    assert patch(row.id, {"name": ".."}).status_code == 422


def test_rename_into_a_clash_in_the_same_folder_is_a_409(repo):
    seed(repo, name="a.txt")
    row = seed(repo, name="b.txt")

    response = patch(row.id, {"name": "A.TXT"})

    assert response.status_code == 409
    assert response.json()["detail"] == "A file named A.TXT already exists here"
    assert row.name == "b.txt"


def test_a_case_only_rename_is_not_a_clash_with_itself(repo):
    row = seed(repo, name="notes.txt")

    assert patch(row.id, {"name": "Notes.txt"}).json()["name"] == "Notes.txt"


def test_the_same_name_is_fine_in_another_folder(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    seed(repo, name="a.txt")
    row = seed(repo, name="a.txt", folder_id=folder.id)

    assert patch(row.id, {"folder_id": None}).status_code == 409
    assert patch(row.id, {"name": "b.txt", "folder_id": None}).status_code == 200


def test_an_empty_description_is_stored_as_null(repo):
    row = seed(repo, description="old")

    response = patch(row.id, {"description": ""})

    assert response.json()["description"] is None
    assert repo.audits[-1][1] == "retag"
    assert repo.audits[-1][4] == {
        "before": {"description": "old", "tags": []},
        "after": {"description": None, "tags": []},
    }


def test_an_overlong_description_is_a_422(repo):
    row = seed(repo)

    assert patch(row.id, {"description": "x" * 2001}).status_code == 422


def test_tags_are_trimmed_lowercased_and_deduplicated_in_order(repo):
    row = seed(repo)

    response = patch(row.id, {"tags": [" Tax ", "2026", "tax", "", "  "]})

    assert response.json()["tags"] == ["tax", "2026"]


def test_too_many_or_too_long_tags_are_a_422(repo):
    row = seed(repo)

    assert patch(row.id, {"tags": [f"t{i}" for i in range(21)]}).status_code == 422
    assert patch(row.id, {"tags": ["x" * 51]}).status_code == 422
    assert patch(row.id, {"tags": ["x" * 50]}).status_code == 200


def test_folder_id_omitted_is_unchanged_and_null_is_the_root(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    row = seed(repo)

    moved = patch(row.id, {"folder_id": str(folder.id)})
    assert moved.json()["folder_id"] == str(folder.id)
    assert repo.audits[-1][1:] == (
        "move",
        "file",
        row.id,
        {"before": None, "after": str(folder.id)},
    )

    assert patch(row.id, {"name": "b.txt"}).json()["folder_id"] == str(folder.id)
    assert patch(row.id, {"folder_id": None}).json()["folder_id"] is None


def test_moving_into_an_unknown_folder_is_a_404(repo):
    row = seed(repo)

    assert patch(row.id, {"folder_id": str(uuid.uuid4())}).status_code == 404
    assert row.folder_id is None


def test_one_patch_writes_one_audit_row_per_action(repo):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    row = seed(repo)

    patch(row.id, {"name": "b.txt", "folder_id": str(folder.id), "tags": ["x"]})

    assert [a[1] for a in repo.audits] == ["rename", "move", "retag"]


def test_an_empty_patch_is_200_and_audits_nothing(repo):
    row = seed(repo)

    assert patch(row.id, {}).status_code == 200
    assert patch(row.id, {"name": "a.txt", "description": None, "tags": []}).status_code == 200
    assert repo.audits == []


def test_patch_of_an_unknown_or_foreign_file_is_a_404(repo):
    row = seed(repo, sub="user-1")

    assert patch(row.id, {"name": "x.txt"}, sub="user-2").status_code == 404
    assert patch(uuid.uuid4(), {"name": "x.txt"}).status_code == 404
    assert row.name == "a.txt"


def test_patch_needs_a_token(repo):
    row = seed(repo)

    assert client.patch(f"/api/files/{row.id}", json={"name": "b.txt"}).status_code == 401


def upload_into(folder_id, name="a.txt", data=b"hello", sub="user-1"):
    return client.post(
        "/api/files",
        headers=auth(sub),
        data={"folder_id": str(folder_id)},
        files={"file": (name, data, "text/plain")},
    )


def test_upload_into_a_folder(repo, store):
    folder = repo.folders.create("user-1", name="A", parent_id=None)

    response = upload_into(folder.id)

    assert response.status_code == 201
    assert response.json()["folder_id"] == str(folder.id)


def test_upload_into_an_unknown_or_foreign_folder_is_a_404(repo, store):
    theirs = repo.folders.create("user-2", name="A", parent_id=None)

    assert upload_into(theirs.id).status_code == 404
    assert upload_into(uuid.uuid4()).status_code == 404
    assert repo.rows == []
    assert store.objects == {}


def test_br2_versions_per_folder(repo, store):
    folder = repo.folders.create("user-1", name="A", parent_id=None)
    at_root = upload(name="notes.txt").json()

    first = upload_into(folder.id, name="notes.txt").json()
    second = upload_into(folder.id, name="notes.txt", data=b"two")

    assert first["id"] != at_root["id"]
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
