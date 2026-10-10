"""Regression R-2 — one household never sees, edits or deletes another's data.

Every ledger row carries a household_id and every repository call takes it.
A foreign id must look exactly like a missing one: 404, never 403.
"""

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)


def auth(sub):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def test_a_foreign_provider_or_service_is_a_404(ledger):
    ledger.households.create("alice", "Maison")
    ledger.households.create("dave", "Chalet")
    provider = client.post("/api/providers", headers=auth("alice"), json={"name": "Bell"}).json()
    service = client.post(
        f"/api/providers/{provider['id']}/services",
        headers=auth("alice"),
        json={"name": "Internet", "category": "internet"},
    ).json()

    attempts = [
        client.get(f"/api/providers/{provider['id']}", headers=auth("dave")),
        client.patch(f"/api/providers/{provider['id']}", headers=auth("dave"), json={"name": "X"}),
        client.delete(f"/api/providers/{provider['id']}", headers=auth("dave")),
        client.post(
            f"/api/providers/{provider['id']}/services",
            headers=auth("dave"),
            json={"name": "Hack", "category": "x"},
        ),
        client.get(f"/api/services/{service['id']}", headers=auth("dave")),
        client.patch(
            f"/api/services/{service['id']}", headers=auth("dave"), json={"archived": True}
        ),
        client.delete(f"/api/services/{service['id']}", headers=auth("dave")),
    ]

    assert [r.status_code for r in attempts] == [404] * 7
    assert client.get("/api/providers", headers=auth("dave")).json() == []
