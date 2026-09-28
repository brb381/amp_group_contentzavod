"""Add isolated Instagram and Dzen public collection jobs.

Revision ID: 0037_instagram_dzen_collection
Revises: 0036_russian_notifications
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0037_instagram_dzen_collection"
down_revision: Union[str, Sequence[str], None] = "0036_russian_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_jobs(platform: str) -> None:
    op.create_table(
        f"{platform}_enrichment_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_id", sa.Uuid(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')",
            name=f"ck_{platform}_enrichment_jobs_state",
        ),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("publication_id"),
    )
    op.create_index(
        f"ix_{platform}_enrichment_jobs_due",
        f"{platform}_enrichment_jobs",
        ["state", "available_at", "created_at"],
    )
    op.create_index(
        f"ix_{platform}_enrichment_jobs_dispatch_id",
        f"{platform}_enrichment_jobs",
        ["dispatch_id"],
    )
    op.create_table(
        f"{platform}_view_collection_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("collection_date", sa.Date(), nullable=False),
        sa.Column("collection_slot", sa.SmallInteger(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_id", sa.Uuid(), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')",
            name=f"ck_{platform}_view_collection_jobs_state",
        ),
        sa.CheckConstraint(
            "collection_slot >= 0 AND collection_slot < 12",
            name=f"ck_{platform}_view_collection_jobs_slot",
        ),
        sa.ForeignKeyConstraint(["publication_id"], ["publications.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publication_id", "collection_date", "collection_slot",
            name=f"uq_{platform}_view_job_publication_date_slot",
        ),
    )
    op.create_index(
        f"ix_{platform}_view_collection_jobs_due",
        f"{platform}_view_collection_jobs",
        ["state", "available_at", "created_at"],
    )
    op.create_index(
        f"ix_{platform}_view_collection_jobs_dispatch_id",
        f"{platform}_view_collection_jobs",
        ["dispatch_id"],
    )


def _grant_runtime_access(platform: str) -> None:
    enrichment = f"{platform}_enrichment_jobs"
    views = f"{platform}_view_collection_jobs"
    worker = f"amp_{platform}_worker"
    op.execute(
        "DO $$ BEGIN EXECUTE format("
        f"'GRANT CONNECT ON DATABASE %I TO {worker}', current_database()); END; $$;"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {worker}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {enrichment}, {views} TO amp_api")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {enrichment}, {views} TO amp_scheduler")
    op.execute(f"GRANT SELECT, UPDATE ON {enrichment}, {views} TO {worker}")
    op.execute(f"GRANT SELECT, UPDATE ON publications TO {worker}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON external_provider_states TO {worker}")
    op.execute(f"GRANT SELECT, INSERT ON view_readings, view_reading_history TO {worker}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON reading_dataset_revision TO {worker}")
    op.execute(f"GRANT SELECT ON program_settings TO {worker}")
    op.execute(f"GRANT SELECT ON {enrichment}, {views} TO amp_monitor")
    op.execute(f"GRANT SELECT, UPDATE ON {enrichment}, {views} TO amp_lifecycle_worker")


def upgrade() -> None:
    for platform in ("instagram", "dzen"):
        _create_jobs(platform)
    op.drop_constraint("ck_view_reading_source", "view_readings", type_="check")
    op.create_check_constraint(
        "ck_view_reading_source",
        "view_readings",
        "source IN ('manual', 'youtube_api', 'tiktok_public', 'vk_public', "
        "'rutube_public', 'instagram_public', 'dzen_public')",
    )
    if op.get_bind().dialect.name == "postgresql":
        for platform in ("instagram", "dzen"):
            _grant_runtime_access(platform)


def downgrade() -> None:
    op.drop_constraint("ck_view_reading_source", "view_readings", type_="check")
    op.create_check_constraint(
        "ck_view_reading_source",
        "view_readings",
        "source IN ('manual', 'youtube_api', 'tiktok_public', 'vk_public', 'rutube_public')",
    )
    for platform in ("dzen", "instagram"):
        op.drop_index(
            f"ix_{platform}_view_collection_jobs_dispatch_id",
            table_name=f"{platform}_view_collection_jobs",
        )
        op.drop_index(
            f"ix_{platform}_view_collection_jobs_due",
            table_name=f"{platform}_view_collection_jobs",
        )
        op.drop_table(f"{platform}_view_collection_jobs")
        op.drop_index(
            f"ix_{platform}_enrichment_jobs_dispatch_id",
            table_name=f"{platform}_enrichment_jobs",
        )
        op.drop_index(
            f"ix_{platform}_enrichment_jobs_due",
            table_name=f"{platform}_enrichment_jobs",
        )
        op.drop_table(f"{platform}_enrichment_jobs")
