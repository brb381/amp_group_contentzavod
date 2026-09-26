"""Add private creator profile avatars.

Revision ID: 0034_creator_profile_avatar
Revises: 0033_rutube_collection
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0034_creator_profile_avatar"
down_revision: Union[str, Sequence[str], None] = "0033_rutube_collection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("creator_profiles", sa.Column("avatar_data", sa.LargeBinary(), nullable=True))
    op.add_column(
        "creator_profiles",
        sa.Column("avatar_content_type", sa.String(length=32), nullable=True),
    )
    op.create_check_constraint(
        "ck_creator_profiles_avatar_shape",
        "creator_profiles",
        "(avatar_data IS NULL AND avatar_content_type IS NULL) OR "
        "(avatar_data IS NOT NULL AND avatar_content_type IN ('image/jpeg', 'image/png', 'image/webp'))",
    )


def downgrade() -> None:
    op.drop_constraint("ck_creator_profiles_avatar_shape", "creator_profiles", type_="check")
    op.drop_column("creator_profiles", "avatar_content_type")
    op.drop_column("creator_profiles", "avatar_data")
