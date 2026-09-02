"""Add publication view readings and YouTube collection jobs.

Revision ID: 0014_view_readings
Revises: 0013_email_dispatch_identity
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0014_view_readings"
down_revision: Union[str, Sequence[str], None] = "0013_email_dispatch_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "view_readings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reporting_period", sa.Date(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("reported_value", sa.BigInteger(), nullable=False),
        sa.Column("accepted_value", sa.BigInteger()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("risk_flags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("source IN ('manual', 'youtube_api')", name="ck_view_reading_source"),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'rejected')", name="ck_view_reading_status"),
        sa.CheckConstraint("reported_value >= 0", name="ck_view_reading_reported_nonnegative"),
        sa.CheckConstraint("accepted_value IS NULL OR accepted_value >= 0", name="ck_view_reading_accepted_nonnegative"),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("publication_id", "idempotency_key", name="uq_view_reading_idempotency"),
    )
    op.create_index("ix_view_readings_period_status", "view_readings", ["reporting_period", "status", "captured_at"])
    op.create_index("ix_view_readings_publication_captured", "view_readings", ["publication_id", "captured_at"])

    op.create_table(
        "view_reading_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("reading_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("old_value", sa.BigInteger()),
        sa.Column("new_value", sa.BigInteger()),
        sa.Column("reason", sa.Text()),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["reading_id"], ["view_readings.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_view_reading_history_reading_created", "view_reading_history", ["reading_id", "created_at"])

    op.create_table(
        "youtube_view_collection_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("collection_date", sa.Date(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("dispatch_id", postgresql.UUID(as_uuid=True)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')", name="ck_youtube_view_collection_job_state"),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("publication_id", "collection_date", name="uq_youtube_view_job_publication_date"),
    )
    op.create_index("ix_youtube_view_collection_jobs_due", "youtube_view_collection_jobs", ["state", "available_at", "created_at"])
    op.create_index("ix_youtube_view_collection_jobs_dispatch_id", "youtube_view_collection_jobs", ["dispatch_id"])


def downgrade() -> None:
    op.drop_table("youtube_view_collection_jobs")
    op.drop_table("view_reading_history")
    op.drop_table("view_readings")
