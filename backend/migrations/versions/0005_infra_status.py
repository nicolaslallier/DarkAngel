"""infra_status and backup_status: the collector's readings.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _checked_at() -> sa.Column:
    return sa.Column(
        "checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "infra_status",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instance", sa.Text(), nullable=False),
        _checked_at(),
        sa.Column("reachable", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Text()),
        sa.Column("environments", sa.Integer()),
        sa.Column("stacks", sa.Integer()),
        sa.Column("error", sa.Text()),
    )
    op.create_index(
        "ix_infra_status_instance_checked_at", "infra_status", ["instance", "checked_at"]
    )
    op.create_table(
        "backup_status",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instance", sa.Text(), nullable=False),
        _checked_at(),
        sa.Column("last_backup_at", sa.DateTime(timezone=True)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("object_key", sa.Text()),
    )
    op.create_index(
        "ix_backup_status_instance_checked_at", "backup_status", ["instance", "checked_at"]
    )


def downgrade() -> None:
    op.drop_table("backup_status")
    op.drop_table("infra_status")
