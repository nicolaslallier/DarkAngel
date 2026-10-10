from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.providers import Invoice, Provider, Service


class ProviderRepository:
    """Household-scoped provider and service queries. `household_id` always
    comes first, like `owner_sub` in FileRepository, so omitting it is a
    TypeError rather than a leak."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_providers(self, household_id: uuid.UUID) -> Sequence[Provider]:
        statement = (
            select(Provider)
            .where(Provider.household_id == household_id)
            .order_by(func.lower(Provider.name), Provider.id)
        )
        return self.db.scalars(statement).all()

    def get_provider(self, household_id: uuid.UUID, provider_id: uuid.UUID) -> Provider | None:
        statement = select(Provider).where(
            Provider.id == provider_id, Provider.household_id == household_id
        )
        return self.db.scalars(statement).one_or_none()

    def create_provider(self, household_id: uuid.UUID, **fields: Any) -> Provider:
        row = Provider(id=uuid.uuid4(), household_id=household_id, **fields)
        self.db.add(row)
        self.db.commit()
        return row

    def update_provider(self, provider: Provider, **changes: Any) -> Provider:
        for field, value in changes.items():
            setattr(provider, field, value)
        self.db.commit()
        return provider

    def delete_provider(self, provider: Provider) -> None:
        self.db.delete(provider)
        self.db.commit()

    def services(
        self,
        household_id: uuid.UUID,
        provider_id: uuid.UUID | None = None,
        include_archived: bool = False,
    ) -> Sequence[Service]:
        statement = select(Service).where(Service.household_id == household_id)
        if provider_id is not None:
            statement = statement.where(Service.provider_id == provider_id)
        if not include_archived:
            statement = statement.where(Service.archived.is_(False))
        return self.db.scalars(statement.order_by(func.lower(Service.name), Service.id)).all()

    def get_service(self, household_id: uuid.UUID, service_id: uuid.UUID) -> Service | None:
        statement = select(Service).where(
            Service.id == service_id, Service.household_id == household_id
        )
        return self.db.scalars(statement).one_or_none()

    def create_service(
        self, household_id: uuid.UUID, provider_id: uuid.UUID, **fields: Any
    ) -> Service:
        row = Service(id=uuid.uuid4(), household_id=household_id, provider_id=provider_id, **fields)
        self.db.add(row)
        self.db.commit()
        return row

    def update_service(self, service: Service, **changes: Any) -> Service:
        for field, value in changes.items():
            setattr(service, field, value)
        self.db.commit()
        return service

    def delete_service(self, service: Service) -> None:
        # invoices.service_id has no cascade: a service with invoices refuses
        # to go (IntegrityError). The route checks service_has_invoices first.
        self.db.delete(service)
        self.db.commit()

    def service_has_invoices(self, service_id: uuid.UUID) -> bool:
        statement = select(Invoice.id).where(Invoice.service_id == service_id).limit(1)
        return self.db.scalar(statement) is not None


def provider_repository(db: Db) -> ProviderRepository:
    return ProviderRepository(db)


ProviderRepo = Annotated[ProviderRepository, Depends(provider_repository)]
