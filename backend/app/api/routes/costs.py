import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app import costs
from app.core.clock import Today
from app.core.household import Reader
from app.repositories.invoices import InvoiceRepo
from app.repositories.providers import ProviderRepo

router = APIRouter(tags=["costs"])

CENTS = Decimal("0.01")


class UpcomingInvoice(BaseModel):
    invoice_id: uuid.UUID
    service_id: uuid.UUID
    provider_name: str
    service_name: str
    total: Decimal
    due_on: date
    overdue: bool


class Renewal(BaseModel):
    service_id: uuid.UUID
    provider_name: str
    service_name: str
    contract_end: date
    days_left: int


class Upcoming(BaseModel):
    invoices: list[UpcomingInvoice]
    renewals: list[Renewal]


class CostPoint(BaseModel):
    invoice_id: uuid.UUID
    month: str
    total: Decimal
    reference: Decimal | None
    flagged: bool


class ServiceCosts(BaseModel):
    expected_monthly_cost: Decimal | None
    threshold_pct: int
    points: list[CostPoint]


class MonthTotal(BaseModel):
    month: str
    total: Decimal


class MonthlyCosts(BaseModel):
    months: list[MonthTotal]


@router.get("/upcoming", response_model=Upcoming)
def upcoming(ctx: Reader, providers: ProviderRepo, invoices: InvoiceRepo, today: Today) -> Upcoming:
    names = {p.id: p.name for p in providers.list_providers(ctx.household_id)}
    services = {s.id: s for s in providers.services(ctx.household_id, include_archived=True)}

    due = []
    for row in invoices.validated(ctx.household_id):
        service = services.get(row.service_id)
        if service is None or row.due_on is None or row.total is None:
            continue
        if costs.is_paid(row.paid_at, service.auto_pay, row.due_on, today):
            continue
        due.append(
            UpcomingInvoice(
                invoice_id=row.id,
                service_id=service.id,
                provider_name=names[service.provider_id],
                service_name=service.name,
                total=row.total,
                due_on=row.due_on,
                overdue=row.due_on < today,
            )
        )
    due.sort(key=lambda i: (i.due_on, str(i.invoice_id)))

    renewals = []
    for service in services.values():
        if service.archived or service.contract_end is None:
            continue
        if service.renewal_reminder_days is None:
            continue
        days_left = (service.contract_end - today).days
        if 0 <= days_left <= service.renewal_reminder_days:
            renewals.append(
                Renewal(
                    service_id=service.id,
                    provider_name=names[service.provider_id],
                    service_name=service.name,
                    contract_end=service.contract_end,
                    days_left=days_left,
                )
            )
    renewals.sort(key=lambda r: (r.contract_end, str(r.service_id)))
    return Upcoming(invoices=due, renewals=renewals)


@router.get("/services/{service_id}/costs", response_model=ServiceCosts)
def service_costs(
    ctx: Reader, providers: ProviderRepo, invoices: InvoiceRepo, service_id: uuid.UUID
) -> ServiceCosts:
    service = providers.get_service(ctx.household_id, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service")
    rows = [
        (r.id, r.issued_on or r.due_on, r.total)
        for r in invoices.validated(ctx.household_id, service_id)
        if r.total is not None and (r.issued_on or r.due_on) is not None
    ]
    points = costs.series(rows, service.expected_monthly_cost, service.alert_threshold_pct)
    return ServiceCosts(
        expected_monthly_cost=service.expected_monthly_cost,
        threshold_pct=service.alert_threshold_pct,
        points=[
            CostPoint(
                invoice_id=p.invoice_id,
                month=costs.month_of(p.when),
                total=p.total,
                reference=p.reference,
                flagged=p.flagged,
            )
            for p in points
        ],
    )


@router.get("/costs/monthly", response_model=MonthlyCosts)
def monthly_costs(ctx: Reader, invoices: InvoiceRepo) -> MonthlyCosts:
    rows = [
        (r.issued_on or r.due_on, r.total)
        for r in invoices.validated(ctx.household_id)
        if r.total is not None and (r.issued_on or r.due_on) is not None
    ]
    return MonthlyCosts(
        months=[
            MonthTotal(month=month, total=total.quantize(CENTS))
            for month, total in costs.monthly_totals(rows)
        ]
    )
