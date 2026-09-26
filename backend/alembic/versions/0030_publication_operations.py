"""Add publication promo issuance records.

Revision ID: 0030_publication_operations
Revises: 0029_vk_collection
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0030_publication_operations"
down_revision: Union[str, Sequence[str], None] = "0029_vk_collection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "publication_promo_issuances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("publication_id", sa.Uuid(), nullable=False),
        sa.Column("marketplace", sa.String(length=32), nullable=False),
        sa.Column("issued_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "marketplace IN ('ozon', 'wildberries', 'yandex_market')",
            name="ck_publication_promo_issuances_marketplace",
        ),
        sa.ForeignKeyConstraint(
            ["issued_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"], ["publications.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publication_id",
            "marketplace",
            name="uq_publication_promo_issuance_marketplace",
        ),
    )
    op.create_index(
        "ix_publication_promo_issuances_publication",
        "publication_promo_issuances",
        ["publication_id", "issued_at"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON publication_promo_issuances TO amp_api"
        )


def downgrade() -> None:
    op.drop_index(
        "ix_publication_promo_issuances_publication",
        table_name="publication_promo_issuances",
    )
    op.drop_table("publication_promo_issuances")