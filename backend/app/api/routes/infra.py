from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.clock import Now
from app.core.config import get_settings
from app.core.household import Reader
from app.models.infra import BackupStatus
from app.repositories.infra import InfraRepo

router = APIRouter(tags=["infra"])


class InstanceStatus(BaseModel):
    name: str
    reachable: bool
    version: str | None
    environments: int | None
    stacks: int | None
    checked_at: datetime
    error: str | None


class BackupInfo(BaseModel):
    instance: str
    last_backup_at: datetime | None
    size_bytes: int | None
    age_hours: float | None
    stale: bool
    checked_at: datetime


class Infra(BaseModel):
    instances: list[InstanceStatus]
    backups: list[BackupInfo]


def _backup(row: BackupStatus, now: datetime, max_age_hours: int) -> BackupInfo:
    age = None if row.last_backup_at is None else (now - row.last_backup_at).total_seconds() / 3600
    return BackupInfo(
        instance=row.instance,
        last_backup_at=row.last_backup_at,
        size_bytes=row.size_bytes,
        age_hours=None if age is None else round(age, 1),
        # No archive at all counts as stale.
        stale=age is None or age > max_age_hours,
        checked_at=row.checked_at,
    )


@router.get("/infra", response_model=Infra)
def infra(ctx: Reader, repo: InfraRepo, now: Now) -> Infra:
    max_age = get_settings().backup_max_age_hours
    return Infra(
        instances=[
            InstanceStatus(
                name=s.instance,
                reachable=s.reachable,
                version=s.version,
                environments=s.environments,
                stacks=s.stacks,
                checked_at=s.checked_at,
                error=s.error,
            )
            for s in repo.latest_statuses()
        ],
        backups=[_backup(b, now, max_age) for b in repo.latest_backups()],
    )
