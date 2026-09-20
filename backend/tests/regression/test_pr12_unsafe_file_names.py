"""Regression — PR #12, MinIO home files.

`_key()` builds an object key by concatenating the caller's `sub` with the
upload's filename. A name of `..`, a name holding a backslash, or a name long
enough to break the key had to be rejected before anything reached MinIO.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


@pytest.mark.parametrize("name", ["", ".", "..", "a\\b", "a/b", "x" * 256])
def test_unsafe_names_are_rejected_before_minio(name, store):
    response = client.post(
        "/api/files",
        headers={"Authorization": f"Bearer {token()}"},
        files={"file": (name, b"hello", "text/plain")},
    )

    assert response.status_code == 422
    assert store.objects == {}
