from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.files import Base


class InfraStatus(Base):
    """One collector reading of a Portainer instance. Append-only."""

    __tablename__ = "infra_status"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instance: Mapped[str] = mapped_column(Text, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    reachable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[str | None] = mapped_column(Text, nullable=True)
    environments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stacks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_infra_status_instance_checked_at", "instance", "checked_at"),)


class BackupStatus(Base):
    """The newest archive of an instance as the collector saw it. Append-only."""

    __tablename__ = "backup_status"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instance: Mapped[str] = mapped_column(Text, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Null = the bucket holds no archive for this instance.
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_backup_status_instance_checked_at", "instance", "checked_at"),)
