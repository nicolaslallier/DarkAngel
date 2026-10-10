"""Unit — upcoming dues, per-service cost history and monthly totals."""

from datetime import date
from decimal import Decimal as D

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import token
from tests.unit.test_invoices import service_for

client = TestClient(app)


def auth(sub="user-1"):
    return {"Authorization": f"Bearer {token(sub=sub)}"}


def invoice(home, service, total, due, issued=None, **overrides):
    return home.ledger.invoices.create(
        home.id,
        uploaded_by="user-1",
        file_id=None,
        service_id=service.id,
        status="validated",
        total=D(total),
        due_on=due,
        issued_on=issued,
        **overrides,
    )


def test_upcoming_lists_unpaid_invoices_by_due_date_and_flags_the_overdue(home, clock):
    service = service_for(home)
    invoice(home, service, "10.00", date(2026, 11, 1))
    invoice(home, service, "20.00", date(2026, 10, 1))
    paid = invoice(home, service, "30.00", date(2026, 9, 1))
    paid.paid_at = date(2026, 9, 2)

    body = client.get("/api/upcoming", headers=auth()).json()

    assert [(i["total"], i["overdue"]) for i in body["invoices"]] == [
        ("20.00", True),
        ("10.00", False),
    ]
    assert body["invoices"][0]["provider_name"] == "Bell"
    assert body["invoices"][0]["service_name"] == "Internet"


def test_upcoming_skips_auto_pay_invoices_already_past_due(home, clock):
    service = service_for(home, auto_pay=True)
    invoice(home, service, "10.00", date(2026, 10, 1))
    invoice(home, service, "20.00", date(2026, 11, 1))

    body = client.get("/api/upcoming", headers=auth()).json()

    assert [i["total"] for i in body["invoices"]] == ["20.00"]


def test_upcoming_ignores_invoices_still_awaiting_validation(home, clock):
    service = service_for(home)
    home.ledger.invoices.create(
        home.id, uploaded_by="user-1", file_id=None, service_id=service.id, status="to_validate"
    )

    assert client.get("/api/upcoming", headers=auth()).json()["invoices"] == []


def test_renewals_appear_inside_their_reminder_window_only(home, clock):
    service_for(home, name="Soon", contract_end=date(2026, 10, 25), renewal_reminder_days=30)
    service_for(home, name="Far", contract_end=date(2026, 12, 31), renewal_reminder_days=30)
    service_for(home, name="Over", contract_end=date(2026, 10, 1), renewal_reminder_days=30)
    service_for(home, name="NoReminder", contract_end=date(2026, 10, 12))
    service_for(
        home,
        name="Archived",
        contract_end=date(2026, 10, 12),
        renewal_reminder_days=30,
        archived=True,
    )

    renewals = client.get("/api/upcoming", headers=auth()).json()["renewals"]

    assert [(r["service_name"], r["days_left"]) for r in renewals] == [("Soon", 15)]


def test_the_cost_history_flags_an_overspend_against_the_mean_of_the_previous_invoices(home):
    service = service_for(home, expected_monthly_cost=D("50.00"))
    for month, total in enumerate(("100", "100", "100", "200"), start=1):
        invoice(home, service, total, date(2026, month, 28), issued=date(2026, month, 1))

    body = client.get(f"/api/services/{service.id}/costs", headers=auth()).json()

    assert (body["expected_monthly_cost"], body["threshold_pct"]) == ("50.00", 20)
    assert [p["month"] for p in body["points"]] == ["2026-01", "2026-02", "2026-03", "2026-04"]
    # Three invoices of history make the mean (100) the reference for the fourth.
    assert [p["flagged"] for p in body["points"]] == [True, True, True, True]
    assert body["points"][3]["reference"] == "100.00"


def test_a_bill_at_the_threshold_is_not_flagged(home):
    service = service_for(home)
    for month, total in enumerate(("100", "100", "100", "120"), start=1):
        invoice(home, service, total, date(2026, month, 28), issued=date(2026, month, 1))

    flags = [
        p["flagged"]
        for p in client.get(f"/api/services/{service.id}/costs", headers=auth()).json()["points"]
    ]

    assert flags[-1] is False


def test_the_threshold_is_the_services_own(home):
    service = service_for(home, alert_threshold_pct=5)
    for month, total in enumerate(("100", "100", "100", "110"), start=1):
        invoice(home, service, total, date(2026, month, 28), issued=date(2026, month, 1))

    points = client.get(f"/api/services/{service.id}/costs", headers=auth()).json()["points"]

    assert points[-1]["flagged"] is True


def test_costs_of_a_foreign_service_are_a_404(home):
    other = home.ledger.households.create("dave", "Chalet")
    foreign = service_for(type("H", (), {"id": other.id, "ledger": home.ledger}))

    assert client.get(f"/api/services/{foreign.id}/costs", headers=auth()).status_code == 404


def test_monthly_totals_sum_every_service(home):
    one, two = service_for(home), service_for(home, name="Mobile")
    invoice(home, one, "10.50", date(2026, 9, 28), issued=date(2026, 9, 1))
    invoice(home, two, "20", date(2026, 9, 30), issued=date(2026, 9, 5))
    invoice(home, one, "5", date(2026, 8, 28))

    months = client.get("/api/costs/monthly", headers=auth()).json()["months"]

    assert months == [{"month": "2026-08", "total": "5.00"}, {"month": "2026-09", "total": "30.50"}]


def test_a_viewer_can_read_all_three(home, clock):
    home.ledger.households.add_member(home.id, "viewer", "viewer")
    service = service_for(home)

    for path in ("/api/upcoming", f"/api/services/{service.id}/costs", "/api/costs/monthly"):
        assert client.get(path, headers=auth("viewer")).status_code == 200
