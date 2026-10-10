"""Unit — the invoice routes, against the ledger fakes, FakeS3 and FakeFileRepository."""

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token

client = TestClient(app)

PDF = b"%PDF-1.4 fake"


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def service_for(home, **overrides):
    """A provider with one service, straight in the fake."""
    providers = home.ledger.providers
    provider = providers.create_provider(
        home.id, name="Bell", website=None, phone=None, email=None, notes=None
    )
    fields = {
        "name": "Internet",
        "category": "internet",
        "account_number": None,
        "contract_start": None,
        "contract_end": None,
        "renewal_reminder_days": None,
        "expected_monthly_cost": None,
        "auto_pay": False,
        "alert_threshold_pct": 20,
        "archived": False,
    } | overrides
    return providers.create_service(home.id, provider.id, **fields)


def upload(names=("facture.pdf",), sub="user-1", **form):
    files = [("files", (name, PDF, "application/pdf")) for name in names]
    return client.post("/api/invoices", headers=auth(sub), files=files, data=form)


def fields(service_id, **overrides):
    return {
        "service_id": str(service_id),
        "total": "114.98",
        "due_on": "2026-11-01",
        "issued_on": "2026-10-01",
        "invoice_number": "A-1",
        "consumption_qty": "1240",
        "consumption_unit": "kWh",
        "taxes": [{"name": "TPS", "amount": "5.00"}, {"name": "TVQ", "amount": "9.98"}],
    } | overrides


def test_uploading_a_pdf_stores_it_as_a_file_and_waits_for_validation(home, repo, store):
    response = upload()

    assert response.status_code == 201
    [invoice] = response.json()
    # No Ollama configured in the unit suite: straight to the validation queue.
    assert (invoice["status"], invoice["has_pdf"], invoice["service_id"]) == (
        "to_validate",
        True,
        None,
    )
    [stored] = repo.rows
    assert (stored.name, stored.owner_sub, stored.content_type) == (
        "facture.pdf",
        "user-1",
        "application/pdf",
    )
    assert store.objects[stored.object_key][0] == PDF


def test_several_pdfs_make_several_invoices_and_a_repeated_name_gets_a_suffix(home, repo, store):
    response = upload(("a.pdf", "a.pdf"))

    assert len(response.json()) == 2
    assert len({row.name for row in repo.rows}) == 2


def test_only_pdfs_are_accepted_and_nothing_is_stored_when_one_is_refused(home, repo, store):
    response = upload(("a.pdf", "b.txt"))

    assert response.status_code == 415
    assert repo.rows == [] and store.objects == {}


def test_a_preselected_service_is_kept(home, repo, store):
    service = service_for(home)

    [invoice] = upload(service_id=str(service.id)).json()

    assert invoice["service_id"] == str(service.id)


def test_a_foreign_preselection_is_a_404_and_stores_nothing(home, repo, store):
    other = home.ledger.households.create("dave", "Chalet")
    foreign = service_for(type("H", (), {"id": other.id, "ledger": home.ledger}))

    response = upload(service_id=str(foreign.id))

    assert response.status_code == 404
    assert repo.rows == []


def test_validating_fills_the_invoice_and_its_taxes(home, repo, store):
    service = service_for(home)
    [pending] = upload().json()

    response = client.post(
        f"/api/invoices/{pending['id']}/validate", headers=auth(), json=fields(service.id)
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["total"], body["service_id"]) == (
        "validated",
        "114.98",
        str(service.id),
    )
    assert [t["name"] for t in body["taxes"]] == ["TPS", "TVQ"]


def test_a_duplicate_number_on_the_same_service_is_a_409_naming_the_existing_invoice(
    home, repo, store
):
    service = service_for(home)
    first = client.post("/api/invoices/manual", headers=auth(), json=fields(service.id)).json()
    [pending] = upload().json()

    response = client.post(
        f"/api/invoices/{pending['id']}/validate", headers=auth(), json=fields(service.id)
    )

    assert response.status_code == 409
    assert response.json()["detail"]["existing_id"] == first["id"]


def test_the_same_number_on_another_service_is_fine(home, repo, store):
    one, two = service_for(home), service_for(home, name="Mobile")
    client.post("/api/invoices/manual", headers=auth(), json=fields(one.id))

    response = client.post("/api/invoices/manual", headers=auth(), json=fields(two.id))

    assert response.status_code == 201


def test_a_validated_invoice_cannot_be_validated_again(home):
    service = service_for(home)
    done = client.post("/api/invoices/manual", headers=auth(), json=fields(service.id)).json()

    response = client.post(
        f"/api/invoices/{done['id']}/validate", headers=auth(), json=fields(service.id)
    )

    assert response.status_code == 409


def test_total_and_due_date_are_required_to_validate(home):
    service = service_for(home)
    body = fields(service.id)
    del body["due_on"]

    assert client.post("/api/invoices/manual", headers=auth(), json=body).status_code == 422


def test_manual_entry_creates_a_validated_invoice_without_a_pdf(home):
    service = service_for(home)

    response = client.post("/api/invoices/manual", headers=auth(), json=fields(service.id))

    assert response.status_code == 201
    assert (response.json()["status"], response.json()["has_pdf"]) == ("validated", False)


def test_marking_paid_and_unpaid(home, clock):
    service = service_for(home)
    invoice = client.post("/api/invoices/manual", headers=auth(), json=fields(service.id)).json()
    assert invoice["paid"] is False

    paid = client.post(
        f"/api/invoices/{invoice['id']}/paid", headers=auth(), json={"paid": True}
    ).json()
    unpaid = client.post(
        f"/api/invoices/{invoice['id']}/paid", headers=auth(), json={"paid": False}
    ).json()

    assert (paid["paid"], paid["paid_at"]) == (True, "2026-10-10")
    assert (unpaid["paid"], unpaid["paid_at"]) == (False, None)


def test_an_auto_pay_service_makes_its_overdue_invoices_paid(home, clock):
    service = service_for(home, auto_pay=True)

    due = client.post(
        "/api/invoices/manual", headers=auth(), json=fields(service.id, due_on="2026-10-09")
    ).json()
    future = client.post(
        "/api/invoices/manual",
        headers=auth(),
        json=fields(service.id, due_on="2026-11-09", invoice_number="A-2"),
    ).json()

    assert (due["paid"], due["paid_at"]) == (True, None)
    assert future["paid"] is False


def test_the_list_filters_by_status_service_and_unpaid(home, repo, store, clock):
    service = service_for(home)
    upload()
    client.post("/api/invoices/manual", headers=auth(), json=fields(service.id))
    paid = client.post(
        "/api/invoices/manual", headers=auth(), json=fields(service.id, invoice_number="A-2")
    ).json()
    client.post(f"/api/invoices/{paid['id']}/paid", headers=auth(), json={"paid": True})

    def ids(**params):
        return {i["id"] for i in client.get("/api/invoices", headers=auth(), params=params).json()}

    assert len(ids()) == 3
    assert len(ids(status="to_validate")) == 1
    assert len(ids(service_id=str(service.id))) == 2
    assert len(ids(unpaid="true")) == 1


def test_a_viewer_can_read_and_stream_the_pdf_but_never_write(home, repo, store):
    home.ledger.households.add_member(home.id, "viewer", "viewer")
    service = service_for(home)
    [pending] = upload().json()

    assert client.get("/api/invoices", headers=auth("viewer")).status_code == 200
    assert client.get(f"/api/invoices/{pending['id']}", headers=auth("viewer")).status_code == 200
    assert client.get(f"/api/invoices/{pending['id']}/pdf", headers=auth("viewer")).content == PDF
    writes = [
        upload(sub="viewer"),
        client.post("/api/invoices/manual", headers=auth("viewer"), json=fields(service.id)),
        client.post(
            f"/api/invoices/{pending['id']}/validate",
            headers=auth("viewer"),
            json=fields(service.id),
        ),
        client.post(
            f"/api/invoices/{pending['id']}/paid", headers=auth("viewer"), json={"paid": True}
        ),
        client.delete(f"/api/invoices/{pending['id']}", headers=auth("viewer")),
    ]
    assert [r.status_code for r in writes] == [403] * 5


def test_another_member_streams_the_pdf_the_uploader_owns(home, repo, store):
    home.ledger.households.add_member(home.id, "user-2", "member")
    [pending] = upload().json()

    response = client.get(f"/api/invoices/{pending['id']}/pdf", headers=auth("user-2"))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content == PDF


def test_a_deleted_pdf_is_a_404_on_the_stream_but_the_invoice_survives(home, repo, store):
    [pending] = upload().json()
    repo.soft_delete(repo.rows[0])

    assert client.get(f"/api/invoices/{pending['id']}/pdf", headers=auth()).status_code == 404
    assert client.get(f"/api/invoices/{pending['id']}", headers=auth()).status_code == 200


def test_another_household_sees_nothing_of_an_invoice(home, repo, store):
    home.ledger.households.create("dave", "Chalet")
    [pending] = upload().json()

    responses = [
        client.get(f"/api/invoices/{pending['id']}", headers=auth("dave")),
        client.get(f"/api/invoices/{pending['id']}/pdf", headers=auth("dave")),
        client.delete(f"/api/invoices/{pending['id']}", headers=auth("dave")),
    ]

    assert [r.status_code for r in responses] == [404] * 3
    assert client.get("/api/invoices", headers=auth("dave")).json() == []


def test_deleting_an_invoice_leaves_the_file(home, repo, store):
    [pending] = upload().json()

    assert client.delete(f"/api/invoices/{pending['id']}", headers=auth()).status_code == 204
    assert client.get("/api/invoices", headers=auth()).json() == []
    assert len(repo.rows) == 1
