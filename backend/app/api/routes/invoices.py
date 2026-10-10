import logging
import uuid
from collections.abc import Iterator
from contextlib import suppress
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from minio.error import S3Error
from pydantic import BaseModel, Field

from app import costs
from app.api.routes import files as file_routes
from app.api.routes.files import _measure, _unversioned, _validated_name
from app.core.clock import Today
from app.core.config import get_settings
from app.core.household import Reader, Writer
from app.models.files import File
from app.models.providers import Invoice
from app.repositories.files import FileRepo, QuotaExceeded
from app.repositories.invoices import InvoiceRepo
from app.repositories.providers import ProviderRepo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])

Status = Literal["queued", "extracting", "to_validate", "validated", "failed"]


class TaxLine(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)


class InvoiceFields(BaseModel):
    """What a person confirms: the same shape validates a queued invoice and
    creates a manual one. Total and due date are what the reminders and the
    chart need, so they are required."""

    service_id: uuid.UUID
    total: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    due_on: date
    issued_on: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    invoice_number: str | None = Field(None, max_length=100)
    consumption_qty: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=3)
    consumption_unit: str | None = Field(None, max_length=30)
    taxes: list[TaxLine] = Field(default_factory=list, max_length=20)


class InvoiceInfo(BaseModel):
    id: uuid.UUID
    status: Status
    service_id: uuid.UUID | None
    has_pdf: bool
    total: Decimal | None
    due_on: date | None
    issued_on: date | None
    period_start: date | None
    period_end: date | None
    invoice_number: str | None
    consumption_qty: Decimal | None
    consumption_unit: str | None
    paid_at: date | None
    paid: bool
    error: str | None
    extraction: dict | None
    taxes: list[TaxLine]

    @classmethod
    def of(cls, row: Invoice, service, taxes, today: date) -> "InvoiceInfo":
        paid = row.status == "validated" and costs.is_paid(
            row.paid_at, bool(service and service.auto_pay), row.due_on, today
        )
        return cls(
            id=row.id,
            status=row.status,
            service_id=row.service_id,
            has_pdf=row.file_id is not None,
            total=row.total,
            due_on=row.due_on,
            issued_on=row.issued_on,
            period_start=row.period_start,
            period_end=row.period_end,
            invoice_number=row.invoice_number,
            consumption_qty=row.consumption_qty,
            consumption_unit=row.consumption_unit,
            paid_at=row.paid_at,
            paid=paid,
            error=row.error,
            extraction=row.extraction,
            taxes=[TaxLine(name=t.name, amount=t.amount) for t in taxes],
        )


class PaidBody(BaseModel):
    paid: bool
    paid_at: date | None = None


def _services(providers: ProviderRepo, ctx) -> dict:
    # ponytail: one query for the whole household; a household has dozens of services
    return {s.id: s for s in providers.services(ctx.household_id, include_archived=True)}


def _infos(ctx, invoices: InvoiceRepo, providers: ProviderRepo, rows, today: date):
    services = _services(providers, ctx)
    taxes = invoices.taxes_by_invoice([r.id for r in rows])
    return [InvoiceInfo.of(r, services.get(r.service_id), taxes.get(r.id, []), today) for r in rows]


def _invoice(invoices: InvoiceRepo, ctx, invoice_id: uuid.UUID) -> Invoice:
    row = invoices.get(ctx.household_id, invoice_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such invoice")
    return row


def _own_service(providers: ProviderRepo, ctx, service_id: uuid.UUID):
    row = providers.get_service(ctx.household_id, service_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service")
    return row


def _reject_duplicate(invoices: InvoiceRepo, service_id, number, exclude_id=None) -> None:
    # ponytail: check-then-write; two racing validations of one number would
    # hit uq_invoices_service_number and surface as a 500, which a household
    # of a few people will not see. Catch IntegrityError if it ever happens.
    if number and (existing := invoices.find_duplicate(service_id, number, exclude_id)):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "message": "An invoice with this number already exists for this service",
                "existing_id": str(existing.id),
            },
        )


def _store_pdf(sub: str, files: FileRepo, upload: UploadFile) -> File:
    """An invoice PDF is a normal file of the uploader's: same quota, same
    storage, same audit trail as the Files page."""
    settings = get_settings()
    name = _validated_name(upload.filename or "")
    size = _measure(upload.file)
    if size > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File is {size} bytes; the limit is {settings.max_upload_bytes}",
        )
    if files.find_by_name(sub, name, None) is not None:
        # A same-name upload to the Files page would become a new *version*;
        # two invoices must stay two files.
        name = f"{name[:-4]}-{uuid.uuid4().hex[:6]}.pdf"
    try:
        row = files.reserve(
            sub,
            name=name,
            folder_id=None,
            size_bytes=size,
            content_type="application/pdf",
            quota_bytes=settings.user_quota_bytes,
        )
    except QuotaExceeded as e:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Quota exceeded: {e.used} of {e.limit} bytes used, {e.needed} more needed",
        ) from e
    try:
        written = file_routes.s3_client().put_object(
            settings.s3_bucket,
            row.object_key,
            upload.file,
            length=size,
            content_type="application/pdf",
        )
    except S3Error as e:
        files.abandon(row)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Storage is unavailable") from e
    if not written.version_id:
        files.abandon(row)
        with suppress(Exception):
            file_routes.s3_client().remove_object(settings.s3_bucket, row.object_key)
        raise _unversioned()
    files.finalize(row, s3_version_id=written.version_id, actor_sub=sub)
    files.audit(sub, "upload", "file", row.id, {"name": name, "size": size})
    return row


@router.post("", response_model=list[InvoiceInfo], status_code=status.HTTP_201_CREATED)
def upload_invoices(
    ctx: Writer,
    invoices: InvoiceRepo,
    providers: ProviderRepo,
    files_repo: FileRepo,
    today: Today,
    background: BackgroundTasks,
    files: list[UploadFile],
    service_id: Annotated[uuid.UUID | None, Form()] = None,
    provider_id: Annotated[uuid.UUID | None, Form()] = None,
) -> list[InvoiceInfo]:
    """One invoice per PDF. A preselected service (or provider, when the drop
    comes from a provider's page) narrows what the AI may propose."""
    for upload in files:
        if not (upload.filename or "").lower().endswith(".pdf"):
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"Only PDF invoices are accepted: {upload.filename}",
            )
    if service_id is not None:
        _own_service(providers, ctx, service_id)
    elif provider_id is not None and providers.get_provider(ctx.household_id, provider_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such provider")

    use_ai = bool(get_settings().ollama_url)
    created: list[Invoice] = []
    for upload in files:
        file_row = _store_pdf(ctx.sub, files_repo, upload)
        row = invoices.create(
            ctx.household_id,
            uploaded_by=ctx.sub,
            file_id=file_row.id,
            service_id=service_id,
            status="queued" if use_ai else "to_validate",
            extraction={"provider_id": str(provider_id)} if provider_id else None,
        )
        created.append(row)
        if use_ai:
            from app import invoice_extraction

            background.add_task(invoice_extraction.extract, row.id)
    return _infos(ctx, invoices, providers, created, today)


@router.post("/manual", response_model=InvoiceInfo, status_code=status.HTTP_201_CREATED)
def create_manual_invoice(
    ctx: Writer, invoices: InvoiceRepo, providers: ProviderRepo, today: Today, body: InvoiceFields
) -> InvoiceInfo:
    service = _own_service(providers, ctx, body.service_id)
    _reject_duplicate(invoices, service.id, body.invoice_number)
    fields = body.model_dump(exclude={"service_id", "taxes"})
    row = invoices.create(
        ctx.household_id,
        uploaded_by=ctx.sub,
        file_id=None,
        service_id=service.id,
        status="validated",
        taxes=[t.model_dump() for t in body.taxes],
        **fields,
    )
    return _infos(ctx, invoices, providers, [row], today)[0]


@router.get("", response_model=list[InvoiceInfo])
def list_invoices(
    ctx: Reader,
    invoices: InvoiceRepo,
    providers: ProviderRepo,
    today: Today,
    status: Status | None = None,
    service_id: uuid.UUID | None = None,
    unpaid: bool = False,
) -> list[InvoiceInfo]:
    rows = invoices.list(ctx.household_id, status=status, service_id=service_id)
    infos = _infos(ctx, invoices, providers, rows, today)
    if unpaid:
        infos = [i for i in infos if i.status == "validated" and not i.paid]
    return infos


@router.get("/{invoice_id}", response_model=InvoiceInfo)
def get_invoice(
    ctx: Reader, invoices: InvoiceRepo, providers: ProviderRepo, today: Today, invoice_id: uuid.UUID
) -> InvoiceInfo:
    row = _invoice(invoices, ctx, invoice_id)
    return _infos(ctx, invoices, providers, [row], today)[0]


@router.get("/{invoice_id}/pdf")
def invoice_pdf(
    ctx: Reader, invoices: InvoiceRepo, files: FileRepo, invoice_id: uuid.UUID
) -> StreamingResponse:
    """The PDF lives in the uploader's files, which other members cannot read
    through /api/files; this endpoint is the household's way to it."""
    row = _invoice(invoices, ctx, invoice_id)
    file_row = files.get(row.uploaded_by, row.file_id) if row.file_id else None
    if file_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The PDF is missing")
    try:
        obj = file_routes.s3_client().get_object(get_settings().s3_bucket, file_row.object_key)
    except S3Error as e:
        if e.code == "NoSuchKey":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "The PDF is missing") from e
        raise

    def chunks() -> Iterator[bytes]:
        try:
            yield from obj.stream(64 * 1024)
        finally:
            obj.close()
            obj.release_conn()

    return StreamingResponse(
        chunks(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "Content-Length": obj.headers["Content-Length"],
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )


@router.post("/{invoice_id}/validate", response_model=InvoiceInfo)
def validate_invoice(
    ctx: Writer,
    invoices: InvoiceRepo,
    providers: ProviderRepo,
    today: Today,
    invoice_id: uuid.UUID,
    body: InvoiceFields,
) -> InvoiceInfo:
    row = _invoice(invoices, ctx, invoice_id)
    if row.status not in ("to_validate", "failed"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This invoice is not awaiting validation")
    service = _own_service(providers, ctx, body.service_id)
    _reject_duplicate(invoices, service.id, body.invoice_number, exclude_id=row.id)
    invoices.validate(
        row,
        service_id=service.id,
        fields=body.model_dump(exclude={"service_id", "taxes"}),
        taxes=[t.model_dump() for t in body.taxes],
    )
    return _infos(ctx, invoices, providers, [row], today)[0]


@router.post("/{invoice_id}/paid", response_model=InvoiceInfo)
def set_paid(
    ctx: Writer,
    invoices: InvoiceRepo,
    providers: ProviderRepo,
    today: Today,
    invoice_id: uuid.UUID,
    body: PaidBody,
) -> InvoiceInfo:
    row = _invoice(invoices, ctx, invoice_id)
    if row.status != "validated":
        raise HTTPException(status.HTTP_409_CONFLICT, "Validate the invoice first")
    invoices.set_paid(row, (body.paid_at or today) if body.paid else None)
    return _infos(ctx, invoices, providers, [row], today)[0]


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(ctx: Writer, invoices: InvoiceRepo, invoice_id: uuid.UUID) -> Response:
    """The invoice goes; its PDF stays in the uploader's Files."""
    invoices.delete(_invoice(invoices, ctx, invoice_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
