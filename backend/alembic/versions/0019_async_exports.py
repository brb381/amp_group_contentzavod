"""Add asynchronous private report exports.

Revision ID: 0019_async_exports
Revises: 0018_payout_event_details
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0019_async_exports"
down_revision: Union[str, Sequence[str], None] = "0018_payout_event_details"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "export_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("export_type", sa.String(length=32), nullable=False),
        sa.Column("export_format", sa.String(length=8), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column(
            "filters",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("dispatch_id", sa.Uuid(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("artifact_key", sa.String(length=512), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("schema_version > 0", name="ck_export_jobs_schema_version"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_export_jobs_attempt_count"),
        sa.CheckConstraint("row_count IS NULL OR row_count >= 0", name="ck_export_jobs_row_count"),
        sa.CheckConstraint("file_size IS NULL OR file_size >= 0", name="ck_export_jobs_file_size"),
        sa.CheckConstraint(
            "status <> 'ready' OR (artifact_key IS NOT NULL AND filename IS NOT NULL "
            "AND content_type IS NOT NULL AND content_sha256 IS NOT NULL "
            "AND file_size IS NOT NULL AND row_count IS NOT NULL "
            "AND data_as_of IS NOT NULL AND completed_at IS NOT NULL "
            "AND expires_at IS NOT NULL)",
            name="ck_export_jobs_ready_artifact",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "requested_by_user_id",
            "idempotency_key",
            name="uq_export_jobs_requester_idempotency",
        ),
    )
    op.create_index(
        "ix_export_jobs_dispatch", "export_jobs", ["status", "available_at", "created_at"]
    )
    op.create_index(
        "ix_export_jobs_requester_created",
        "export_jobs",
        ["requested_by_user_id", "created_at"],
    )
    op.create_index(
        "ix_export_jobs_expiry", "export_jobs", ["status", "expires_at"]
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $validate_export_role$
        DECLARE role_data record;
        BEGIN
            SELECT * INTO role_data FROM pg_roles WHERE rolname = 'amp_export_worker';
            IF role_data IS NULL THEN
                RAISE EXCEPTION 'Missing AMP runtime database role: amp_export_worker';
            END IF;
            IF NOT role_data.rolcanlogin OR role_data.rolsuper OR role_data.rolcreatedb
                OR role_data.rolcreaterole OR role_data.rolreplication
                OR role_data.rolbypassrls
                OR EXISTS (
                    SELECT 1 FROM pg_auth_members WHERE member = role_data.oid
                )
            THEN
                RAISE EXCEPTION 'Unsafe amp_export_worker role configuration';
            END IF;
        END
        $validate_export_role$;
        """
    )
    op.execute(
        """
        DO $grant_export_connect$
        BEGIN
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO amp_export_worker', current_database()
            );
        END
        $grant_export_connect$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO amp_export_worker")
    op.execute("GRANT SELECT, UPDATE ON TABLE export_jobs TO amp_scheduler")
    op.execute("GRANT SELECT, UPDATE ON TABLE export_jobs TO amp_export_worker")
    op.execute("GRANT SELECT ON TABLE payout_requests, users TO amp_export_worker")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE ALL PRIVILEGES ON TABLES FROM amp_export_worker"
    )
    op.execute(
        """
        CREATE FUNCTION prevent_export_artifact_fact_rewrite()
        RETURNS trigger AS $$
        BEGIN
            IF (OLD.artifact_key IS NOT NULL AND NEW.artifact_key IS DISTINCT FROM OLD.artifact_key)
                OR (OLD.filename IS NOT NULL AND NEW.filename IS DISTINCT FROM OLD.filename)
                OR (OLD.content_type IS NOT NULL AND NEW.content_type IS DISTINCT FROM OLD.content_type)
                OR (OLD.content_sha256 IS NOT NULL AND NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256)
                OR (OLD.file_size IS NOT NULL AND NEW.file_size IS DISTINCT FROM OLD.file_size)
                OR (OLD.row_count IS NOT NULL AND NEW.row_count IS DISTINCT FROM OLD.row_count)
                OR (OLD.data_as_of IS NOT NULL AND NEW.data_as_of IS DISTINCT FROM OLD.data_as_of)
                OR (OLD.completed_at IS NOT NULL AND NEW.completed_at IS DISTINCT FROM OLD.completed_at)
                OR (OLD.expires_at IS NOT NULL AND NEW.expires_at IS DISTINCT FROM OLD.expires_at)
            THEN
                RAISE EXCEPTION 'export artifact facts are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_export_artifact_facts_immutable
        BEFORE UPDATE ON export_jobs
        FOR EACH ROW EXECUTE FUNCTION prevent_export_artifact_fact_rewrite()
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Migration 0019_async_exports is irreversible because export audit facts "
        "must not be silently discarded."
    )
