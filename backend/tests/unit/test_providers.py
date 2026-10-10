"""Unit — the provider and service routes, against FakeProviderRepository."""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)

PROVIDER = {
    "name": "Bell",
    "website": "https://bell.ca",
    "phone": None,
    "email": None,
    "notes": None,
}
SERVICE = {
    "name": "Internet",
    "category": "internet",
    "auto_pay": True,
    "expected_monthly_cost": "79.99",
}


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def make_provider(body=None, sub="user-1"):
    return client.post("/api/providers", headers=auth(sub), json=body or PROVIDER)


def make_service(provider_id, body=None, sub="user-1"):
    return client.post(
        f"/api/providers/{provider_id}/services", headers=auth(sub), json=body or SERVICE
    )


def test_create_and_read_a_provider_with_its_services(home):
    provider = make_provider().json()
    make_service(provider["id"])

    listed = client.get("/api/providers", headers=auth()).json()

    assert [p["name"] for p in listed] == ["Bell"]
    assert [s["name"] for s in listed[0]["services"]] == ["Internet"]
    assert listed[0]["services"][0]["alert_threshold_pct"] == 20
    assert listed[0]["services"][0]["expected_monthly_cost"] == "79.99"


def test_providers_are_listed_by_name_ignoring_case(home):
    for name in ("hydro", "Bell", "Vidéotron"):
        make_provider({**PROVIDER, "name": name})

    names = [p["name"] for p in client.get("/api/providers", headers=auth()).json()]

    assert names == ["Bell", "hydro", "Vidéotron"]


def test_a_blank_name_is_refused(home):
    assert make_provider({**PROVIDER, "name": ""}).status_code == 422


def test_patch_changes_only_what_is_sent(home):
    provider = make_provider().json()

    body = client.patch(
        f"/api/providers/{provider['id']}", headers=auth(), json={"phone": "1 888 310-2355"}
    ).json()

    assert (body["name"], body["phone"], body["website"]) == (
        "Bell",
        "1 888 310-2355",
        "https://bell.ca",
    )


def test_patch_cannot_blank_the_name(home):
    provider = make_provider().json()

    response = client.patch(f"/api/providers/{provider['id']}", headers=auth(), json={"name": None})

    assert response.status_code == 422


def test_a_provider_with_services_cannot_be_deleted(home):
    provider = make_provider().json()
    service = make_service(provider["id"]).json()

    assert client.delete(f"/api/providers/{provider['id']}", headers=auth()).status_code == 409
    assert client.delete(f"/api/services/{service['id']}", headers=auth()).status_code == 204
    assert client.delete(f"/api/providers/{provider['id']}", headers=auth()).status_code == 204


def test_the_contract_cannot_end_before_it_starts(home):
    provider = make_provider().json()

    response = make_service(
        provider["id"], {**SERVICE, "contract_start": "2026-05-01", "contract_end": "2026-01-01"}
    )

    assert response.status_code == 422


def test_patching_the_end_date_is_checked_against_the_stored_start(home):
    provider = make_provider().json()
    service = make_service(provider["id"], {**SERVICE, "contract_start": "2026-05-01"}).json()

    response = client.patch(
        f"/api/services/{service['id']}", headers=auth(), json={"contract_end": "2026-01-01"}
    )

    assert response.status_code == 422


def test_a_service_is_archived_with_a_patch(home):
    provider = make_provider().json()
    service = make_service(provider["id"]).json()

    client.patch(f"/api/services/{service['id']}", headers=auth(), json={"archived": True})

    listed = client.get(f"/api/providers/{provider['id']}", headers=auth()).json()
    assert listed["services"][0]["archived"] is True


def test_a_service_with_invoices_is_not_deleted_but_archived(home):
    provider = make_provider().json()
    service = make_service(provider["id"]).json()
    home.ledger.invoices.create(
        home.id,
        uploaded_by="user-1",
        file_id=None,
        service_id=uuid.UUID(service["id"]),
        status="validated",
    )

    assert client.delete(f"/api/services/{service['id']}", headers=auth()).status_code == 409


def test_a_viewer_reads_but_never_writes(home):
    home.ledger.households.add_member(home.id, "viewer", "viewer")
    provider = make_provider().json()
    service = make_service(provider["id"]).json()

    assert client.get("/api/providers", headers=auth("viewer")).status_code == 200
    assert client.get(f"/api/services/{service['id']}", headers=auth("viewer")).status_code == 200
    writes = [
        client.post("/api/providers", headers=auth("viewer"), json=PROVIDER),
        client.patch(
            f"/api/providers/{provider['id']}", headers=auth("viewer"), json={"notes": "x"}
        ),
        client.delete(f"/api/providers/{provider['id']}", headers=auth("viewer")),
        client.post(
            f"/api/providers/{provider['id']}/services", headers=auth("viewer"), json=SERVICE
        ),
        client.patch(
            f"/api/services/{service['id']}", headers=auth("viewer"), json={"archived": True}
        ),
        client.delete(f"/api/services/{service['id']}", headers=auth("viewer")),
    ]
    assert [r.status_code for r in writes] == [403] * 6


def test_a_member_of_the_household_sees_what_the_owner_created(home):
    home.ledger.households.add_member(home.id, "user-2", "member")
    make_provider()

    assert len(client.get("/api/providers", headers=auth("user-2")).json()) == 1
