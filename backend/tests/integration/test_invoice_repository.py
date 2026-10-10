"""Integration — InvoiceRepository against real PostgreSQL."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.repositories.households import HouseholdRepository
from app.repositories.invoices import InvoiceRepository
from app.repositories.providers import ProviderRepository


@pytest.fixture
def setup(db):
    household = HouseholdRepository(db).create("alice", "Maison").id
    providers = ProviderRepository(db)
    bell = providers.create_provider(
        household, name="Bell", website=None, phone=None, email=None, notes=None
    )
    service = providers.create_service(
        household,
        bell.id,
        name="Internet",
        category="internet",
        account_number=None,
        contract_start=None,
        contract_end=None,
        renewal_reminder_days=None,
        expected_monthly_cost=None,
        auto_pay=False,
        alert_threshold_pct=20,
        archived=False,
    )
    return household, service.id, InvoiceRepository(db)


def make(invoices, household, service_id, status="validated", **fields):
    return invoices.create(
        household, uploaded_by="alice", file_id=None, service_id=service_id, status=status, **fields
    )


def test_create_with_taxes_and_read_them_back(setup):
    household, service_id, invoices = setup

    row = make(
        invoices,
        household,
        service_id,
        total=Decimal("114.98"),
        due_on=date(2026, 11, 1),
        taxes=[
            {"name": "TPS", "amount": Decimal("5.00")},
            {"name": "TVQ", "amount": Decimal("9.98")},
        ],
    )

    taxes = invoices.taxes_by_invoice([row.id])[row.id]
    assert sorted((t.name, t.amount) for t in taxes) == [
        ("TPS", Decimal("5.00")),
        ("TVQ", Decimal("9.98")),
    ]


def test_lists_are_scoped_and_filtered(setup, db):
    household, service_id, invoices = setup
    other = HouseholdRepository(db).create("dave", "Chalet").id
    make(invoices, household, service_id, due_on=date(2026, 11, 1), total=Decimal("1"))
    make(invoices, household, None, status="to_validate")

    assert len(invoices.list(household)) == 2
    assert [i.status for i in invoices.list(household, status="to_validate")] == ["to_validate"]
    assert len(invoices.list(household, service_id=service_id)) == 1
    assert invoices.list(other) == []


def test_find_duplicate_matches_service_and_number(setup):
    household, service_id, invoices = setup
    first = make(invoices, household, service_id, invoice_number="A-1", total=Decimal("1"))

    assert invoices.find_duplicate(service_id, "A-1").id == first.id
    assert invoices.find_duplicate(service_id, "A-1", exclude_id=first.id) is None
    assert invoices.find_duplicate(service_id, "A-2") is None


def test_validate_fills_the_fields_replaces_taxes_and_sets_the_status(setup):
    household, service_id, invoices = setup
    pending = make(invoices, household, None, status="to_validate")

    invoices.validate(
        pending,
        service_id=service_id,
        fields={"total": Decimal("50.00"), "due_on": date(2026, 11, 1)},
        taxes=[{"name": "TPS", "amount": Decimal("2.50")}],
    )

    assert (pending.status, pending.service_id, pending.total) == (
        "validated",
        service_id,
        Decimal("50.00"),
    )
    assert [t.name for t in invoices.taxes_by_invoice([pending.id])[pending.id]] == ["TPS"]


def test_set_paid_and_unpaid(setup):
    household, service_id, invoices = setup
    row = make(invoices, household, service_id, total=Decimal("1"), due_on=date(2026, 11, 1))

    invoices.set_paid(row, date(2026, 10, 10))
    assert row.paid_at == date(2026, 10, 10)
    invoices.set_paid(row, None)
    assert row.paid_at is None


def test_validated_is_ordered_oldest_first_and_filterable(setup):
    household, service_id, invoices = setup
    late = make(invoices, household, service_id, total=Decimal("2"), issued_on=date(2026, 5, 1))
    early = make(invoices, household, service_id, total=Decimal("1"), issued_on=date(2026, 1, 1))
    make(invoices, household, None, status="to_validate")

    assert [i.id for i in invoices.validated(household)] == [early.id, late.id]
    assert [i.id for i in invoices.validated(household, service_id)] == [early.id, late.id]


def test_finish_stores_the_result(setup):
    household, service_id, invoices = setup
    row = make(invoices, household, None, status="extracting")

    invoices.finish(row, "to_validate", extraction={"raw": {}}, fields={"total": Decimal("9.99")})

    assert (row.status, row.total, row.extraction) == ("to_validate", Decimal("9.99"), {"raw": {}})
    assert invoices.by_id(row.id).status == "to_validate"
    assert invoices.by_id(uuid.uuid4()) is None


def test_reset_stuck_fails_queued_and_extracting_only(setup, db):
    household, service_id, invoices = setup
    queued = make(invoices, household, None, status="queued")
    extracting = make(invoices, household, None, status="extracting")
    waiting = make(invoices, household, None, status="to_validate")

    count = invoices.reset_stuck()
    db.expire_all()

    assert count == 2
    assert (queued.status, extracting.status, waiting.status) == ("failed", "failed", "to_validate")
    assert queued.error


def test_delete_removes_the_invoice_and_its_taxes(setup):
    household, service_id, invoices = setup
    row = make(
        invoices,
        household,
        service_id,
        total=Decimal("1"),
        taxes=[{"name": "TPS", "amount": Decimal("1")}],
    )

    invoices.delete(row)

    assert invoices.get(household, row.id) is None
    assert invoices.taxes_by_invoice([row.id]) == {}
