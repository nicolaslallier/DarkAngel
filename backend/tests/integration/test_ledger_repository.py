"""Integration — ProviderRepository against real PostgreSQL, and the schema's cascades."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.providers import Invoice
from app.repositories.households import HouseholdRepository
from app.repositories.providers import ProviderRepository


@pytest.fixture
def mine(db):
    return HouseholdRepository(db).create("alice", "Maison").id


@pytest.fixture
def theirs(db):
    return HouseholdRepository(db).create("dave", "Chalet").id


@pytest.fixture
def providers(db):
    return ProviderRepository(db)


def make_service(providers, household_id, provider_id, **overrides):
    fields = {
        "name": "Internet",
        "category": "internet",
        "account_number": None,
        "contract_start": None,
        "contract_end": None,
        "renewal_reminder_days": None,
        "expected_monthly_cost": Decimal("79.99"),
        "auto_pay": False,
        "alert_threshold_pct": 20,
        "archived": False,
    } | overrides
    return providers.create_service(household_id, provider_id, **fields)


def test_providers_are_scoped_to_the_household(providers, mine, theirs):
    bell = providers.create_provider(
        mine, name="Bell", website=None, phone=None, email=None, notes=None
    )

    assert providers.get_provider(mine, bell.id).name == "Bell"
    assert providers.get_provider(theirs, bell.id) is None
    assert providers.list_providers(theirs) == []


def test_services_hide_archived_ones_unless_asked(providers, mine):
    bell = providers.create_provider(
        mine, name="Bell", website=None, phone=None, email=None, notes=None
    )
    make_service(providers, mine, bell.id)
    make_service(providers, mine, bell.id, name="Mobile", archived=True)

    assert [s.name for s in providers.services(mine)] == ["Internet"]
    assert len(providers.services(mine, include_archived=True)) == 2


def test_a_service_with_an_invoice_reports_it_and_cannot_be_deleted(providers, mine, db):
    bell = providers.create_provider(
        mine, name="Bell", website=None, phone=None, email=None, notes=None
    )
    service = make_service(providers, mine, bell.id)
    db.add(
        Invoice(
            id=uuid.uuid4(),
            household_id=mine,
            service_id=service.id,
            uploaded_by="alice",
            status="validated",
        )
    )
    db.commit()

    assert providers.service_has_invoices(service.id) is True
    with pytest.raises(IntegrityError):
        providers.delete_service(service)


def test_deleting_a_household_removes_everything_under_it(providers, mine, db):
    bell = providers.create_provider(
        mine, name="Bell", website=None, phone=None, email=None, notes=None
    )
    service = make_service(providers, mine, bell.id)
    db.add(
        Invoice(
            id=uuid.uuid4(),
            household_id=mine,
            service_id=service.id,
            uploaded_by="alice",
            status="validated",
        )
    )
    db.commit()

    HouseholdRepository(db).delete(mine)

    for table in ("providers", "services", "invoices"):
        assert db.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 0


def test_the_database_refuses_an_unknown_invoice_status(mine, db):
    db.add(Invoice(id=uuid.uuid4(), household_id=mine, uploaded_by="alice", status="bogus"))

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
