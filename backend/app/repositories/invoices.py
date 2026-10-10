from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.providers import Invoice, InvoiceTax


class InvoiceRepository:
    """Household-scoped invoice queries. Two methods are deliberately unscoped
    and only the background job and the startup sweep use them: `by_id`
    (the job already holds an invoice id from a scoped request) and
    `reset_stuck` (a janitor, like FileRepository.sweep_pending)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        household_id: uuid.UUID,
        *,
        uploaded_by: str,
        file_id: uuid.UUID | None,
        service_id: uuid.UUID | None,
        status: str,
        extraction: dict | None = None,
        taxes: Sequence[dict] = (),
        **fields: Any,
    ) -> Invoice:
        row = Invoice(
            id=uuid.uuid4(),
            household_id=household_id,
            uploaded_by=uploaded_by,
            file_id=file_id,
            service_id=service_id,
            status=status,
            extraction=extraction,
            **fields,
        )
        self.db.add(row)
        self.db.add_all(InvoiceTax(id=uuid.uuid4(), invoice_id=row.id, **tax) for tax in taxes)
        self.db.commit()
        return row

    def get(self, household_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice | None:
        statement = select(Invoice).where(
            Invoice.id == invoice_id, Invoice.household_id == household_id
        )
        return self.db.scalars(statement).one_or_none()

    def by_id(self, invoice_id: uuid.UUID) -> Invoice | None:
        return self.db.get(Invoice, invoice_id)

    def list(
        self,
        household_id: uuid.UUID,
        *,
        status: str | None = None,
        service_id: uuid.UUID | None = None,
    ) -> Sequence[Invoice]:
        statement = select(Invoice).where(Invoice.household_id == household_id)
        if status is not None:
            statement = statement.where(Invoice.status == status)
        if service_id is not None:
            statement = statement.where(Invoice.service_id == service_id)
        statement = statement.order_by(
            Invoice.due_on.asc().nulls_last(), Invoice.created_at, Invoice.id
        )
        return self.db.scalars(statement).all()

    def taxes_by_invoice(
        self, invoice_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[InvoiceTax]]:
        found: dict[uuid.UUID, list[InvoiceTax]] = defaultdict(list)
        if invoice_ids:
            statement = (
                select(InvoiceTax)
                .where(InvoiceTax.invoice_id.in_(invoice_ids))
                .order_by(InvoiceTax.name, InvoiceTax.id)
            )
            for tax in self.db.scalars(statement):
                found[tax.invoice_id].append(tax)
        return dict(found)

    def find_duplicate(
        self,
        service_id: uuid.UUID,
        invoice_number: str,
        exclude_id: uuid.UUID | None = None,
    ) -> Invoice | None:
        statement = select(Invoice).where(
            Invoice.service_id == service_id, Invoice.invoice_number == invoice_number
        )
        if exclude_id is not None:
            statement = statement.where(Invoice.id != exclude_id)
        return self.db.scalars(statement.limit(1)).one_or_none()

    def validate(
        self,
        invoice: Invoice,
        *,
        service_id: uuid.UUID,
        fields: dict[str, Any],
        taxes: Sequence[dict],
    ) -> Invoice:
        for field, value in fields.items():
            setattr(invoice, field, value)
        invoice.service_id = service_id
        invoice.status = "validated"
        invoice.error = None
        self.db.execute(delete(InvoiceTax).where(InvoiceTax.invoice_id == invoice.id))
        self.db.add_all(InvoiceTax(id=uuid.uuid4(), invoice_id=invoice.id, **tax) for tax in taxes)
        self.db.commit()
        return invoice

    def set_paid(self, invoice: Invoice, paid_at: date | None) -> None:
        invoice.paid_at = paid_at
        self.db.commit()

    def delete(self, invoice: Invoice) -> None:
        self.db.delete(invoice)
        self.db.commit()

    def validated(
        self, household_id: uuid.UUID, service_id: uuid.UUID | None = None
    ) -> Sequence[Invoice]:
        """Oldest first, by issue date (due date when the issue date is unknown)."""
        statement = select(Invoice).where(
            Invoice.household_id == household_id, Invoice.status == "validated"
        )
        if service_id is not None:
            statement = statement.where(Invoice.service_id == service_id)
        statement = statement.order_by(func.coalesce(Invoice.issued_on, Invoice.due_on), Invoice.id)
        return self.db.scalars(statement).all()

    def set_status(self, invoice: Invoice, status: str) -> None:
        invoice.status = status
        self.db.commit()

    def finish(
        self,
        invoice: Invoice,
        status: str,
        *,
        extraction: dict | None = None,
        error: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> bool:
        """Record the background read's outcome, but only while the invoice is
        still waiting for it: a late finish must not undo a validation, a
        deletion or the restart sweep. False when it did not apply."""
        values: dict[str, Any] = {**(fields or {}), "status": status, "error": error}
        if extraction is not None:
            values["extraction"] = extraction
        result = self.db.execute(
            update(Invoice)
            .where(Invoice.id == invoice.id, Invoice.status.in_(("queued", "extracting")))
            .values(**values)
        )
        self.db.commit()
        self.db.refresh(invoice)
        return result.rowcount == 1

    def reset_stuck(self) -> int:
        """Background tasks die with the process: whatever was queued or
        running at a restart will never finish."""
        result = self.db.execute(
            update(Invoice)
            .where(Invoice.status.in_(("queued", "extracting")))
            .values(status="failed", error="Interrupted by a restart")
        )
        self.db.commit()
        return result.rowcount


def invoice_repository(db: Db) -> InvoiceRepository:
    return InvoiceRepository(db)


InvoiceRepo = Annotated[InvoiceRepository, Depends(invoice_repository)]
