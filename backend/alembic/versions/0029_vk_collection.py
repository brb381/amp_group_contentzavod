"""Add isolated VK enrichment and public view collection jobs.

Revision ID: 0029_vk_collection
Revises: 0028_tiktok_collection
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0029_vk_collection"
down_revision: Union[str, Sequence[str], None] = "0028_tiktok_collection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _job_columns(*, include_collection_date: bool) -> list[sa.Column]:
    columns = [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
    ]
    if include_collection_date:
        columns.append(sa.Column("collection_date", sa.Date(), nullable=False))
    columns.extend(
        [
            sa.Column("state", sa.String(length=20), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False),
            sa.Column(
                "available_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("dispatch_id", sa.Uuid(), nullable=True),
            sa.Column("last_error_code", sa.String(length=100), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        ]
    )
    return columns


def upgrade() -> None:
    op.create_table(
        "vk_enrichment_jobs",
        *_job_columns(include_collection_date=False),
        sa.CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')",
            name="ck_vk_enrichment_jobs_state",
        ),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("publication_id"),
    )
    op.create_index(
        "ix_vk_enrichment_jobs_due",
        "vk_enrichment_jobs",
        ["state", "available_at", "created_at"],
    )
    op.create_index(
        "ix_vk_enrichment_jobs_dispatch_id",
        "vk_enrichment_jobs",
        ["dispatch_id"],
    )
    op.create_table(
        "vk_view_collection_jobs",
        *_job_columns(include_collection_date=True),
        sa.CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')",
            name="ck_vk_view_collection_jobs_state",
        ),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "publication_id", "collection_date", name="uq_vk_view_job_publication_date"
        ),
    )
    op.create_index(
        "ix_vk_view_collection_jobs_due",
        "vk_view_collection_jobs",
        ["state", "available_at", "created_at"],
    )
    op.create_index(
        "ix_vk_view_collection_jobs_dispatch_id",
        "vk_view_collection_jobs",
        ["dispatch_id"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DO $$ BEGIN EXECUTE format("
            "'GRANT CONNECT ON DATABASE %I TO amp_vk_worker', current_database()); END; $$;"
        )
        op.execute("GRANT USAGE ON SCHEMA public TO amp_vk_worker")
        op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON vk_enrichment_jobs, vk_view_collection_jobs TO amp_api")
        op.execute("GRANT SELECT, INSERT, UPDATE ON vk_enrichment_jobs, vk_view_collection_jobs TO amp_scheduler")
        op.execute("GRANT SELECT ON publications TO amp_scheduler")
        op.execute("GRANT SELECT, UPDATE ON vk_enrichment_jobs, vk_view_collection_jobs TO amp_vk_worker")
        op.execute("GRANT SELECT, UPDATE ON publications TO amp_vk_worker")
        op.execute("GRANT SELECT, INSERT, UPDATE ON external_provider_states TO amp_vk_worker")
        op.execute("GRANT SELECT, INSERT ON view_readings, view_reading_history TO amp_vk_worker")
        op.execute("GRANT SELECT, INSERT, UPDATE ON reading_dataset_revision TO amp_vk_worker")
        op.execute("GRANT SELECT ON vk_enrichment_jobs, vk_view_collection_jobs TO amp_monitor")
        op.execute("GRANT SELECT, UPDATE ON vk_enrichment_jobs, vk_view_collection_jobs TO amp_lifecycle_worker")


def downgrade() -> None:
    op.drop_index(
        "ix_vk_view_collection_jobs_dispatch_id",
        table_name="vk_view_collection_jobs",
    )
    op.drop_index("ix_vk_view_collection_jobs_due", table_name="vk_view_collection_jobs")
    op.drop_table("vk_view_collection_jobs")
    op.drop_index("ix_vk_enrichment_jobs_dispatch_id", table_name="vk_enrichment_jobs")
    op.drop_index("ix_vk_enrichment_jobs_due", table_name="vk_enrichment_jobs")
    op.drop_table("vk_enrichment_jobs")
