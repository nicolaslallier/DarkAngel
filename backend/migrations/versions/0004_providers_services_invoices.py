"""providers, services, invoices, invoice_taxes.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "household_id",
            UUID,
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("website", sa.Text()),
        sa.Column("phone", sa.Text()),
        sa.Column("email", sa.Text()),
        sa.Column("notes", sa.Text()),
        _created_at(),
    )
    op.create_index("ix_providers_household_id", "providers", ["household_id"])

    op.create_table(
        "services",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "provider_id", UUID, sa.ForeignKey("providers.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "household_id",
            UUID,
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("account_number", sa.Text()),
        sa.Column("contract_start", sa.Date()),
        sa.Column("contract_end", sa.Date()),
        sa.Column("renewal_reminder_days", sa.Integer()),
        sa.Column("expected_monthly_cost", sa.Numeric(12, 2)),
        sa.Column("auto_pay", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("alert_threshold_pct", sa.Integer(), server_default="20", nullable=False),
        sa.Column("archived", sa.Boolean(), server_default="false", nullable=False),
        _created_at(),
    )
    op.create_index("ix_services_provider_id", "services", ["provider_id"])
    op.create_index("ix_services_household_id", "services", ["household_id"])

    op.create_table(
        "invoices",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "household_id",
            UUID,
            sa.ForeignKey("households.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("service_id", UUID, sa.ForeignKey("services.id")),
        sa.Column("file_id", UUID, sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("uploaded_by", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("total", sa.Numeric(12, 2)),
        sa.Column("due_on", sa.Date()),
        sa.Column("issued_on", sa.Date()),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date()),
        sa.Column("invoice_number", sa.Text()),
        sa.Column("consumption_qty", sa.Numeric(14, 3)),
        sa.Column("consumption_unit", sa.Text()),
        sa.Column("paid_at", sa.Date()),
        sa.Column("extraction", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('queued', 'extracting', 'to_validate', 'validated', 'failed')",
            name="ck_invoices_status",
        ),
        sa.UniqueConstraint("service_id", "invoice_number", name="uq_invoices_service_number"),
    )
    op.create_index("ix_invoices_household_id", "invoices", ["household_id"])
    op.create_index("ix_invoices_service_id", "invoices", ["service_id"])

    op.create_table(
        "invoice_taxes",
        sa.Column("id", UUID, primary_key=True),
        sa.Column(
            "invoice_id", UUID, sa.ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
    )
    op.create_index("ix_invoice_taxes_invoice_id", "invoice_taxes", ["invoice_id"])


def downgrade() -> None:
    op.drop_table("invoice_taxes")
    op.drop_table("invoices")
    op.drop_table("services")
    op.drop_table("providers")
