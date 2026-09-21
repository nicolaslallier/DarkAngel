"""Regression — PR #12, unsafe file names.

Originally: `_key()` concatenated the caller's `sub` with the upload's
filename, so `..` or a slash in that filename could escape the owner's prefix,
and had to be rejected before anything reached MinIO.

Since the metadata moved to Postgres the object key is `<sub>/<uuid>` and the
filename is only ever a display string, so traversal is structurally
impossible rather than filtered. This test now pins *that* -- a hostile name
is stored verbatim as a label while the key stays a UUID under the right
owner -- plus the display rules that survived.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


def post(name):
    return client.post(
        "/api/files",
        headers={"Authorization": f"Bearer {token()}"},
        files={"file": (name, b"hello", "text/plain")},
    )


@pytest.mark.parametrize("name", ["../../etc/passwd", "a/b", "a\\b", "..\\..\\x"])
def test_a_hostile_name_cannot_shape_the_object_key(name, repo, store):
    response = post(name)

    assert response.status_code == 201
    assert response.json()["name"] == name

    key = next(iter(store.objects))
    owner, _, suffix = key.partition("/")
    assert owner == "user-1"
    uuid.UUID(suffix)  # raises if the key is anything but a plain UUID


@pytest.mark.parametrize("name", ["", "   ", ".", "..", "x" * 256, "bad\x7fname"])
def test_undisplayable_names_are_still_rejected(name, repo, store):
    # \x7f (DEL) rather than \x00: httpx's multipart writer percent-encodes
    # ASCII control bytes 0x00-0x1F in the filename header (so a NUL never
    # reaches the server as a literal byte through this client) but leaves
    # 0x7F alone, so it is the one control character this transport can
    # actually exercise end to end.
    assert post(name).status_code == 422
    assert store.objects == {}
