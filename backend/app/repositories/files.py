from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import Db
from app.models.files import AuditLog, File, FileVersion


class QuotaExceeded(Exception):
    def __init__(self, used: int, limit: int, needed: int) -> None:
        self.used, self.limit, self.needed = used, limit, needed
        super().__init__(f"{used} of {limit} bytes used, {needed} more needed")


class FileRepository:
    """Every query is scoped to one owner, and `owner_sub` is always the first
    argument so that omitting it is a TypeError rather than a data leak.

    This is the only thing separating two users now that the MinIO key prefix
    no longer does it structurally -- see docs/files-feature.md risk R-1.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- reads ---

    def list(self, owner_sub: str, *, limit: int = 100, offset: int = 0) -> Sequence[File]:
        statement = (
            select(File)
            .where(
                File.owner_sub == owner_sub,
                File.deleted_at.is_(None),
                File.status == "ready",
            )
            .order_by(File.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return self.db.scalars(statement).all()

    def get(self, owner_sub: str, file_id: uuid.UUID) -> File | None:
        statement = select(File).where(
            File.id == file_id,
            File.owner_sub == owner_sub,
            File.deleted_at.is_(None),
            File.status == "ready",
        )
        return self.db.scalars(statement).one_or_none()

    def find_by_name(self, owner_sub: str, name: str, folder_id: uuid.UUID | None) -> File | None:
        statement = select(File).where(
            File.owner_sub == owner_sub,
            func.lower(File.name) == name.lower(),
            File.folder_id.is_(folder_id) if folder_id is None else File.folder_id == folder_id,
            File.deleted_at.is_(None),
            File.status == "ready",
        )
        return self.db.scalars(statement).one_or_none()

    def used_bytes(self, owner_sub: str) -> int:
        # Pending rows count: the pending insert is the quota reservation.
        statement = select(func.coalesce(func.sum(File.size_bytes), 0)).where(
            File.owner_sub == owner_sub, File.deleted_at.is_(None)
        )
        return self.db.scalar(statement) or 0

    # --- writes ---

    def reserve(
        self,
        owner_sub: str,
        *,
        name: str,
        folder_id: uuid.UUID | None,
        size_bytes: int,
        content_type: str,
        quota_bytes: int,
    ) -> File:
        """Check the quota and claim the space, in one short transaction.

        The advisory lock serialises concurrent uploads by the same owner, so
        two requests cannot both read an under-quota total and both insert. It
        is transaction-scoped and released at the commit below -- long before
        the caller starts streaming bytes to MinIO.
        """
        self.db.execute(select(func.pg_advisory_xact_lock(func.hashtext(owner_sub))))

        used = self.used_bytes(owner_sub)
        if used + size_bytes > quota_bytes:
            self.db.rollback()
            raise QuotaExceeded(used, quota_bytes, size_bytes)

        file_id = uuid.uuid4()
        row = File(
            id=file_id,
            owner_sub=owner_sub,
            folder_id=folder_id,
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            object_key=f"{owner_sub}/{file_id}",
            status="pending",
        )
        self.db.add(row)
        self.db.commit()
        return row

    def finalize(self, file: File, *, s3_version_id: str, actor_sub: str) -> File:
        version = FileVersion(
            id=uuid.uuid4(),
            file_id=file.id,
            version_no=1,
            s3_version_id=s3_version_id,
            size_bytes=file.size_bytes,
            content_type=file.content_type,
            created_by=actor_sub,
        )
        self.db.add(version)
        file.status = "ready"
        file.current_version_id = version.id
        self.db.commit()
        return file

    def add_version(
        self,
        file: File,
        *,
        s3_version_id: str,
        size_bytes: int,
        content_type: str,
        actor_sub: str,
    ) -> File:
        highest = (
            self.db.scalar(
                select(func.max(FileVersion.version_no)).where(FileVersion.file_id == file.id)
            )
            or 0
        )
        version = FileVersion(
            id=uuid.uuid4(),
            file_id=file.id,
            version_no=highest + 1,
            s3_version_id=s3_version_id,
            size_bytes=size_bytes,
            content_type=content_type,
            created_by=actor_sub,
        )
        self.db.add(version)
        file.size_bytes = size_bytes
        file.content_type = content_type
        file.current_version_id = version.id
        file.updated_at = datetime.now(UTC)
        self.db.commit()
        return file

    def abandon(self, file: File) -> None:
        self.db.delete(file)
        self.db.commit()

    def soft_delete(self, file: File) -> None:
        file.deleted_at = datetime.now(UTC)
        self.db.commit()

    def sweep_pending(self, older_than_seconds: int = 3600) -> list[str]:
        """Drop reservations whose upload never finished, returning the object
        keys the caller should try to delete from MinIO."""
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        stale = self.db.scalars(
            select(File).where(File.status == "pending", File.created_at < cutoff)
        ).all()
        keys = [row.object_key for row in stale]
        for row in stale:
            self.db.delete(row)
        self.db.commit()
        return keys

    def audit(
        self,
        actor_sub: str,
        action: str,
        target_type: str,
        target_id: uuid.UUID,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            AuditLog(
                actor_sub=actor_sub,
                action=action,
                target_type=target_type,
                target_id=target_id,
                detail=detail,
            )
        )
        self.db.commit()


def file_repository(db: Db) -> FileRepository:
    return FileRepository(db)


FileRepo = Annotated[FileRepository, Depends(file_repository)]
