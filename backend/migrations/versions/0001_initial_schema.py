"""Initial schema: folders, files, file_versions, audit_log.

Hand-written. Autogenerate cannot express the NULLS NOT DISTINCT partial
indexes or the append-only trigger below, and would offer to drop them on
every subsequent revision.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_sub", sa.Text(), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("folders.id")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_folders_owner_sub", "folders", ["owner_sub"])

    op.create_table(
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_sub", sa.Text(), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("folders.id")),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "file_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("s3_version_id", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.UniqueConstraint("file_id", "version_no", name="uq_file_versions_no"),
    )
    op.create_index("ix_file_versions_file_id", "file_versions", ["file_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actor_sub", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("detail", postgresql.JSONB()),
    )
    op.create_index("ix_audit_log_actor_sub", "audit_log", ["actor_sub"])
    op.create_index("ix_audit_log_target_id", "audit_log", ["target_id"])

    # NULLS NOT DISTINCT (PG 15+): without it, two root folders -- both with a
    # NULL parent_id -- could share a name, because Postgres normally treats
    # every NULL as different from every other NULL.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_folders_sibling_name
            ON folders (owner_sub, parent_id, lower(name))
            NULLS NOT DISTINCT
            WHERE deleted_at IS NULL
        """
    )

    # Ready files only: a stale pending upload must never lock a user out of a
    # name, and a trashed file must not either.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_files_folder_name
            ON files (owner_sub, folder_id, lower(name))
            NULLS NOT DISTINCT
            WHERE deleted_at IS NULL AND status = 'ready'
        """
    )

    # The listing query: owner + folder, newest first.
    op.execute(
        """
        CREATE INDEX ix_files_listing
            ON files (owner_sub, folder_id, updated_at DESC)
            WHERE deleted_at IS NULL AND status = 'ready'
        """
    )
    op.execute("CREATE INDEX ix_files_tags ON files USING GIN (tags)")
    op.execute("CREATE INDEX ix_files_pending ON files (created_at) WHERE status = 'pending'")

    # Append-only, enforced by the database rather than by convention. A
    # trigger rather than a REVOKE, because the application role owns these
    # tables and could simply grant the privilege back to itself.
    op.execute(
        """
        CREATE FUNCTION audit_log_is_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_no_change
            BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_change ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_is_append_only()")
    op.drop_table("audit_log")
    op.drop_table("file_versions")
    op.drop_table("files")
    op.drop_table("folders")
