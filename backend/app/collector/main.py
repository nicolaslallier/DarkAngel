from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

from minio import Minio

from app.collector import backups, portainer
from app.core.config import PortainerInstance, get_settings
from app.core.db import session_factory
from app.repositories.infra import InfraRepository

log = logging.getLogger("app.collector")

RETENTION = timedelta(days=30)


def collect_once(
    repo: InfraRepository,
    instances: list[PortainerInstance],
    backup_client: Minio,
    bucket: str,
    now: datetime | None = None,
) -> None:
    now = now or datetime.now(UTC)
    for instance in instances:
        name = portainer.slug(instance.name)
        reading = portainer.check(instance)
        repo.add_status(
            instance=name,
            reachable=reading.reachable,
            version=reading.version,
            environments=reading.environments,
            stacks=reading.stacks,
            error=reading.error,
            checked_at=now,
        )
        try:
            archive = backups.newest(backup_client, bucket, name)
        except Exception:
            # An unreadable bucket reads as "no backup" until the next pass.
            log.exception("could not list backups of %s", name)
            archive = None
        repo.add_backup(
            instance=name,
            last_backup_at=archive.last_backup_at if archive else None,
            size_bytes=archive.size_bytes if archive else None,
            object_key=archive.object_key if archive else None,
            checked_at=now,
        )
    repo.prune(now - RETENTION)


def run() -> None:
    settings = get_settings()
    if not settings.portainer_instances:
        log.warning("no portainer_instances configured; the collector only idles")
    client = Minio(
        settings.backup_s3_endpoint,
        access_key=settings.backup_s3_access_key,
        secret_key=settings.backup_s3_secret_key.get_secret_value(),
        secure=settings.backup_s3_secure,
    )
    while True:
        try:
            with session_factory()() as db:
                collect_once(
                    InfraRepository(db),
                    settings.portainer_instances,
                    client,
                    settings.backup_s3_bucket,
                )
        except Exception:
            # Database down, or the tables do not exist yet (first deploy: the
            # API migrates, the collector may start first). The next pass retries.
            log.exception("collection pass failed")
        time.sleep(settings.collector_interval_seconds)
