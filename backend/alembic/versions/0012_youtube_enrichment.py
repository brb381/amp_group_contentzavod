"""Add isolated YouTube enrichment jobs and quota state.

Revision ID: 0012_youtube_enrichment
Revises: 0011_publications
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0012_youtube_enrichment"
down_revision: Union[str, Sequence[str], None] = "0011_publications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

enrichment_status = postgresql.ENUM(
    "not_requested",
    "pending",
    "processing",
    "succeeded",
    "retry_wait",
    "failed",
    name="publicationenrichmentstatus",
    create_type=False,
)


def upgrade() -> None:
    enrichment_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "publications",
        sa.Column(
            "enrichment_status",
            enrichment_status,
            nullable=False,
            server_default="not_requested",
        ),
    )
    op.add_column("publications", sa.Column("external_title", sa.String(500)))
    op.add_column("publications", sa.Column("external_author_id", sa.String(255)))
    op.add_column("publications", sa.Column("external_author_name", sa.String(500)))
    op.add_column("publications", sa.Column("external_published_at", sa.DateTime(timezone=True)))
    op.add_column("publications", sa.Column("external_duration_seconds", sa.Integer()))
    op.add_column("publications", sa.Column("external_thumbnail_url", sa.String(2048)))
    op.add_column("publications", sa.Column("external_etag", sa.String(255)))
    op.add_column("publications", sa.Column("enriched_at", sa.DateTime(timezone=True)))
    op.add_column("publications", sa.Column("enrichment_error_code", sa.String(100)))

    op.create_table(
        "youtube_enrichment_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("dispatch_id", postgresql.UUID(as_uuid=True)),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')",
            name="ck_youtube_enrichment_jobs_state",
        ),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_youtube_enrichment_jobs_due",
        "youtube_enrichment_jobs",
        ["state", "available_at", "created_at"],
    )
    op.create_index(
        "ix_youtube_enrichment_jobs_dispatch_id",
        "youtube_enrichment_jobs",
        ["dispatch_id"],
    )

    op.create_table(
        "external_provider_states",
        sa.Column("provider", sa.String(50), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="available"),
        sa.Column("blocked_until", sa.DateTime(timezone=True)),
        sa.Column("block_reason", sa.String(100)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "status IN ('available', 'blocked')", name="ck_external_provider_state"
        ),
    )
    op.bulk_insert(
        sa.table(
            "external_provider_states",
            sa.column("provider", sa.String()),
            sa.column("status", sa.String()),
        ),
        [{"provider": "youtube", "status": "available"}],
    )
    op.create_table(
        "external_quota_usage",
        sa.Column("provider", sa.String(50), primary_key=True),
        sa.Column("quota_date", sa.Date(), primary_key=True),
        sa.Column("reserved_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("working_limit", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("external_quota_usage")
    op.drop_table("external_provider_states")
    op.drop_index(
        "ix_youtube_enrichment_jobs_dispatch_id", table_name="youtube_enrichment_jobs"
    )
    op.drop_index("ix_youtube_enrichment_jobs_due", table_name="youtube_enrichment_jobs")
    op.drop_table("youtube_enrichment_jobs")
    for column in (
        "enrichment_error_code",
        "enriched_at",
        "external_etag",
        "external_thumbnail_url",
        "external_duration_seconds",
        "external_published_at",
        "external_author_name",
        "external_author_id",
        "external_title",
        "enrichment_status",
    ):
        op.drop_column("publications", column)
    enrichment_status.drop(op.get_bind(), checkfirst=True)
