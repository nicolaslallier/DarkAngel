import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.household import Reader, Writer
from app.models.providers import Service
from app.repositories.providers import ProviderRepo

router = APIRouter(tags=["providers"])


class ServiceInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_id: uuid.UUID
    name: str
    category: str
    account_number: str | None
    contract_start: date | None
    contract_end: date | None
    renewal_reminder_days: int | None
    expected_monthly_cost: Decimal | None
    auto_pay: bool
    alert_threshold_pct: int
    archived: bool


class ProviderInfo(BaseModel):
    id: uuid.UUID
    name: str
    website: str | None
    phone: str | None
    email: str | None
    notes: str | None
    services: list[ServiceInfo]


class ProviderIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    website: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=254)
    notes: str | None = Field(None, max_length=4000)


class ProviderPatch(BaseModel):
    """Omitted = unchanged."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(None, min_length=1, max_length=200)
    website: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=254)
    notes: str | None = Field(None, max_length=4000)


class ServiceIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    account_number: str | None = Field(None, max_length=100)
    contract_start: date | None = None
    contract_end: date | None = None
    renewal_reminder_days: int | None = Field(None, ge=0, le=365)
    expected_monthly_cost: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    auto_pay: bool = False
    alert_threshold_pct: int = Field(20, ge=0, le=1000)


class ServicePatch(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(None, min_length=1, max_length=200)
    category: str | None = Field(None, min_length=1, max_length=100)
    account_number: str | None = Field(None, max_length=100)
    contract_start: date | None = None
    contract_end: date | None = None
    renewal_reminder_days: int | None = Field(None, ge=0, le=365)
    expected_monthly_cost: Decimal | None = Field(None, ge=0, max_digits=12, decimal_places=2)
    auto_pay: bool | None = None
    alert_threshold_pct: int | None = Field(None, ge=0, le=1000)
    archived: bool | None = None


def _check_dates(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "The contract cannot end before it starts"
        )


def _not_blank(changes: dict, *fields: str) -> None:
    """An explicit null on a required column is a mistake, not 'unchanged'."""
    for field in fields:
        if field in changes and changes[field] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field} is required")


def _provider(repo, ctx, provider_id):
    row = repo.get_provider(ctx.household_id, provider_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such provider")
    return row


def _service(repo, ctx, service_id):
    row = repo.get_service(ctx.household_id, service_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service")
    return row


def _provider_info(repo: ProviderRepo, ctx, provider) -> ProviderInfo:
    services = repo.services(ctx.household_id, provider.id, include_archived=True)
    return ProviderInfo(
        id=provider.id,
        name=provider.name,
        website=provider.website,
        phone=provider.phone,
        email=provider.email,
        notes=provider.notes,
        services=[ServiceInfo.model_validate(s) for s in services],
    )


@router.get("/providers", response_model=list[ProviderInfo])
def list_providers(ctx: Reader, repo: ProviderRepo) -> list[ProviderInfo]:
    return [_provider_info(repo, ctx, p) for p in repo.list_providers(ctx.household_id)]


@router.post("/providers", response_model=ProviderInfo, status_code=status.HTTP_201_CREATED)
def create_provider(ctx: Writer, repo: ProviderRepo, body: ProviderIn) -> ProviderInfo:
    row = repo.create_provider(ctx.household_id, **body.model_dump())
    return _provider_info(repo, ctx, row)


@router.get("/providers/{provider_id}", response_model=ProviderInfo)
def get_provider(ctx: Reader, repo: ProviderRepo, provider_id: uuid.UUID) -> ProviderInfo:
    return _provider_info(repo, ctx, _provider(repo, ctx, provider_id))


@router.patch("/providers/{provider_id}", response_model=ProviderInfo)
def update_provider(
    ctx: Writer, repo: ProviderRepo, provider_id: uuid.UUID, body: ProviderPatch
) -> ProviderInfo:
    row = _provider(repo, ctx, provider_id)
    changes = body.model_dump(exclude_unset=True)
    _not_blank(changes, "name")
    if changes:
        repo.update_provider(row, **changes)
    return _provider_info(repo, ctx, row)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(ctx: Writer, repo: ProviderRepo, provider_id: uuid.UUID) -> Response:
    row = _provider(repo, ctx, provider_id)
    if repo.services(ctx.household_id, provider_id, include_archived=True):
        raise HTTPException(status.HTTP_409_CONFLICT, "Delete or archive its services first")
    repo.delete_provider(row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/providers/{provider_id}/services",
    response_model=ServiceInfo,
    status_code=status.HTTP_201_CREATED,
)
def create_service(
    ctx: Writer, repo: ProviderRepo, provider_id: uuid.UUID, body: ServiceIn
) -> ServiceInfo:
    _provider(repo, ctx, provider_id)
    _check_dates(body.contract_start, body.contract_end)
    row = repo.create_service(ctx.household_id, provider_id, archived=False, **body.model_dump())
    return ServiceInfo.model_validate(row)


@router.get("/services/{service_id}", response_model=ServiceInfo)
def get_service(ctx: Reader, repo: ProviderRepo, service_id: uuid.UUID) -> ServiceInfo:
    return ServiceInfo.model_validate(_service(repo, ctx, service_id))


@router.patch("/services/{service_id}", response_model=ServiceInfo)
def update_service(
    ctx: Writer, repo: ProviderRepo, service_id: uuid.UUID, body: ServicePatch
) -> ServiceInfo:
    row: Service = _service(repo, ctx, service_id)
    changes = body.model_dump(exclude_unset=True)
    _not_blank(changes, "name", "category", "auto_pay", "alert_threshold_pct", "archived")
    _check_dates(
        changes.get("contract_start", row.contract_start),
        changes.get("contract_end", row.contract_end),
    )
    if changes:
        repo.update_service(row, **changes)
    return ServiceInfo.model_validate(row)


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(ctx: Writer, repo: ProviderRepo, service_id: uuid.UUID) -> Response:
    row = _service(repo, ctx, service_id)
    if repo.service_has_invoices(row.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "It has invoices: archive it instead")
    repo.delete_service(row)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
