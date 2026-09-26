"""Collect platform views in two-hour slots.

Revision ID: 0035_two_hour_view_collection
Revises: 0034_creator_profile_avatar
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0035_two_hour_view_collection"
down_revision: Union[str, Sequence[str], None] = "0034_creator_profile_avatar"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JOB_TABLES = (
    (
        "youtube_view_collection_jobs",
        "uq_youtube_view_job_publication_date",
        "uq_youtube_view_job_publication_date_slot",
        "ck_youtube_view_collection_job_slot",
    ),
    (
        "tiktok_view_collection_jobs",
        "uq_tiktok_view_job_publication_date",
        "uq_tiktok_view_job_publication_date_slot",
        "ck_tiktok_view_collection_jobs_slot",
    ),
    (
        "vk_view_collection_jobs",
        "uq_vk_view_job_publication_date",
        "uq_vk_view_job_publication_date_slot",
        "ck_vk_view_collection_jobs_slot",
    ),
    (
        "rutube_view_collection_jobs",
        "uq_rutube_view_job_publication_date",
        "uq_rutube_view_job_publication_date_slot",
        "ck_rutube_view_collection_jobs_slot",
    ),
)


def upgrade() -> None:
    for table, old_unique, new_unique, slot_check in JOB_TABLES:
        op.add_column(
            table,
            sa.Column(
                "collection_slot",
                sa.SmallInteger(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )
        op.execute(
            sa.text(
                f"""
                UPDATE {table}
                SET collection_slot = (
                    EXTRACT(HOUR FROM created_at AT TIME ZONE 'Europe/Moscow')::int / 2
                )::smallint
                """
            )
        )
        op.create_check_constraint(
            slot_check,
            table,
            "collection_slot >= 0 AND collection_slot < 12",
        )
        op.drop_constraint(old_unique, table, type_="unique")
        op.create_unique_constraint(
            new_unique,
            table,
            ["publication_id", "collection_date", "collection_slot"],
        )
        op.alter_column(table, "collection_slot", server_default=None)


def downgrade() -> None:
    for table, old_unique, new_unique, slot_check in reversed(JOB_TABLES):
        op.drop_constraint(new_unique, table, type_="unique")
        op.execute(
            sa.text(
                f"""
                DELETE FROM {table} AS candidate
                USING {table} AS keeper
                WHERE candidate.publication_id = keeper.publication_id
                  AND candidate.collection_date = keeper.collection_date
                  AND (
                    candidate.created_at < keeper.created_at
                    OR (candidate.created_at = keeper.created_at AND candidate.id < keeper.id)
                  )
                """
            )
        )
        op.drop_constraint(slot_check, table, type_="check")
        op.drop_column(table, "collection_slot")
        op.create_unique_constraint(
            old_unique,
            table,
            ["publication_id", "collection_date"],
        )