from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.infra import BackupStatus, InfraStatus


class InfraRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add_status(
        self,
        *,
        instance: str,
        reachable: bool,
        version: str | None = None,
        environments: int | None = None,
        stacks: int | None = None,
        error: str | None = None,
        checked_at: datetime | None = None,
    ) -> InfraStatus:
        row = InfraStatus(
            instance=instance,
            reachable=reachable,
            version=version,
            environments=environments,
            stacks=stacks,
            error=error,
            checked_at=checked_at or datetime.now(UTC),
        )
        self.db.add(row)
        self.db.commit()
        return row

    def add_backup(
        self,
        *,
        instance: str,
        last_backup_at: datetime | None,
        size_bytes: int | None,
        object_key: str | None,
        checked_at: datetime | None = None,
    ) -> BackupStatus:
        row = BackupStatus(
            instance=instance,
            last_backup_at=last_backup_at,
            size_bytes=size_bytes,
            object_key=object_key,
            checked_at=checked_at or datetime.now(UTC),
        )
        self.db.add(row)
        self.db.commit()
        return row

    def latest_statuses(self) -> list[InfraStatus]:
        statement = (
            select(InfraStatus)
            .ext(distinct_on(InfraStatus.instance))
            .order_by(InfraStatus.instance, InfraStatus.checked_at.desc(), InfraStatus.id.desc())
        )
        return list(self.db.scalars(statement))

    def latest_backups(self) -> list[BackupStatus]:
        statement = (
            select(BackupStatus)
            .ext(distinct_on(BackupStatus.instance))
            .order_by(BackupStatus.instance, BackupStatus.checked_at.desc(), BackupStatus.id.desc())
        )
        return list(self.db.scalars(statement))

    def prune(self, before: datetime) -> int:
        deleted = 0
        for model in (InfraStatus, BackupStatus):
            deleted += self.db.execute(delete(model).where(model.checked_at < before)).rowcount
        self.db.commit()
        return deleted


def infra_repository(db: Db) -> InfraRepository:
    return InfraRepository(db)


InfraRepo = Annotated[InfraRepository, Depends(infra_repository)]
