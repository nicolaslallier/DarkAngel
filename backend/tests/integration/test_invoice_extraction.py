"""Integration — extract() end to end against real PostgreSQL, with the PDF read and
the model faked (Ollama and a real PDF are not what is under test)."""

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app import invoice_extraction as ie
from app.core.db import engine
from app.models.files import File
from app.repositories.files import FileRepository
from app.repositories.households import HouseholdRepository
from app.repositories.invoices import InvoiceRepository
from app.repositories.providers import ProviderRepository


@pytest.fixture
def queued(db):
    household = HouseholdRepository(db).create("alice", "Maison").id
    providers = ProviderRepository(db)
    bell = providers.create_provider(
        household, name="Bell", website=None, phone=None, email=None, notes=None
    )
    providers.create_service(
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
    files = FileRepository(db)
    pdf = files.reserve(
        "alice",
        name="facture.pdf",
        folder_id=None,
        size_bytes=10,
        content_type="application/pdf",
        quota_bytes=10**9,
    )
    files.finalize(pdf, s3_version_id="v1", actor_sub="alice")
    invoice = InvoiceRepository(db).create(
        household, uploaded_by="alice", file_id=pdf.id, service_id=None, status="queued"
    )
    return invoice.id


def reread(db, invoice_id):
    db.expire_all()
    return InvoiceRepository(db).by_id(invoice_id)


def test_a_readable_invoice_lands_in_the_validation_queue(queued, db, monkeypatch):
    monkeypatch.setattr(ie, "_pdf_text", lambda key, size: "Bell Internet 114,98 $")
    monkeypatch.setattr(
        ie,
        "_ask",
        lambda text: {
            "provider": "Bell",
            "service": "Internet",
            "total": "114,98 $",
            "due_on": "2026-11-01",
            "invoice_number": "A-1",
            "taxes": [{"name": "TPS", "amount": "5.00"}],
        },
    )

    ie.extract(queued)

    row = reread(db, queued)
    assert (row.status, str(row.total), row.invoice_number) == ("to_validate", "114.98", "A-1")
    assert row.extraction["candidates"]["provider"]["name"] == "Bell"
    assert row.extraction["raw"]["taxes"] == [{"name": "TPS", "amount": "5.00"}]
    json.dumps(row.extraction)


@pytest.mark.parametrize(
    ("text", "ask", "message"),
    [
        ("", lambda t: {}, "no readable text"),
        (
            "some text",
            lambda t: (_ for _ in ()).throw(ValueError("not json")),
            "The invoice could not be read",
        ),
    ],
)
def test_an_unreadable_invoice_fails_with_a_message_and_can_still_be_filled_by_hand(
    queued, db, monkeypatch, text, ask, message
):
    monkeypatch.setattr(ie, "_pdf_text", lambda key, size: text)
    monkeypatch.setattr(ie, "_ask", ask)

    ie.extract(queued)

    row = reread(db, queued)
    assert row.status == "failed"
    assert message in row.error


def test_a_missing_pdf_fails_cleanly(queued, db, monkeypatch):
    db.get(File, reread(db, queued).file_id).deleted_at = datetime.now(UTC)
    db.commit()

    ie.extract(queued)

    assert reread(db, queued).status == "failed"


def test_an_unknown_invoice_id_is_ignored(db):
    ie.extract(uuid.uuid4())


def test_an_unexpected_error_never_reaches_the_client(queued, db, monkeypatch):
    monkeypatch.setattr(ie, "_pdf_text", lambda key, size: "text")
    monkeypatch.setattr(ie, "_ask", lambda text: (_ for _ in ()).throw(RuntimeError("secret sql")))

    ie.extract(queued)

    row = reread(db, queued)
    assert row.status == "failed"
    assert row.error == "The invoice could not be read"
    assert "secret" not in row.error


def test_a_number_already_used_by_the_preselected_service_is_left_for_the_person(
    queued, db, monkeypatch
):
    invoices = InvoiceRepository(db)
    service = ProviderRepository(db).services(reread(db, queued).household_id)[0]
    invoices.create(
        service.household_id,
        uploaded_by="alice",
        file_id=None,
        service_id=service.id,
        status="validated",
        invoice_number="A-1",
    )
    reread(db, queued).service_id = service.id
    db.commit()
    monkeypatch.setattr(ie, "_pdf_text", lambda key, size: "text")
    monkeypatch.setattr(ie, "_ask", lambda text: {"total": "10", "invoice_number": "A-1"})

    ie.extract(queued)

    row = reread(db, queued)
    assert (row.status, row.invoice_number) == ("to_validate", None)


def test_no_connection_is_held_open_while_the_pdf_is_read_and_the_model_answers(
    queued, db, monkeypatch
):
    db.rollback()  # the fixture's own session must not count as idle in transaction
    idle = []

    def watch():
        with engine().connect() as other:
            idle.append(
                other.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction' "
                        "AND datname = current_database() AND pid <> pg_backend_pid()"
                    )
                ).scalar()
            )

    def pdf_text(key, size):
        watch()
        return "text"

    def ask(text_):
        watch()
        return {"total": "10"}

    monkeypatch.setattr(ie, "_pdf_text", pdf_text)
    monkeypatch.setattr(ie, "_ask", ask)

    ie.extract(queued)

    assert idle == [0, 0]
    assert reread(db, queued).status == "to_validate"
