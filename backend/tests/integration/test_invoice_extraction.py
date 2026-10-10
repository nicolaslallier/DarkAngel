"""Integration — extract() end to end against real PostgreSQL, with the PDF read and
the model faked (Ollama and a real PDF are not what is under test)."""

import json
import uuid
from datetime import UTC, datetime

import pytest

from app import invoice_extraction as ie
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
        ("some text", lambda t: (_ for _ in ()).throw(ValueError("not json")), "not json"),
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
