"""Allow public TikTok and VK view reading sources.

Revision ID: 0032_public_reading_sources
Revises: 0031_program_settings
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0032_public_reading_sources"
down_revision: Union[str, Sequence[str], None] = "0031_program_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_view_reading_source", "view_readings", type_="check")
    op.create_check_constraint(
        "ck_view_reading_source",
        "view_readings",
        "source IN ('manual', 'youtube_api', 'tiktok_public', 'vk_public')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_view_reading_source", "view_readings", type_="check")
    op.create_check_constraint(
        "ck_view_reading_source",
        "view_readings",
        "source IN ('manual', 'youtube_api')",
    )