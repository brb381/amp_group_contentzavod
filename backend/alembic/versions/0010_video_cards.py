"""Add video cards.

Revision ID: 0010_video_cards
Revises: 0009_product_catalog
Create Date: 2026-08-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0010_video_cards"
down_revision: Union[str, Sequence[str], None] = "0009_product_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

product_brand = postgresql.ENUM("AMP", "AirTone", "CrioLight", name="productbrand", create_type=False)


def upgrade() -> None:
    op.create_table(
        "video_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reported_brand", product_brand, nullable=True),
        sa.Column("reported_product_name", sa.String(length=255), nullable=True),
        sa.Column("product_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_video_cards_title_not_blank"),
        sa.CheckConstraint(
            "(reported_brand IS NULL AND reported_product_name IS NULL) OR "
            "(reported_brand IS NOT NULL AND reported_product_name IS NOT NULL)",
            name="ck_video_cards_reported_product_complete",
        ),
        sa.CheckConstraint(
            "product_id IS NOT NULL OR "
            "(reported_product_name IS NOT NULL AND length(trim(reported_product_name)) > 0)",
            name="ck_video_cards_product_required",
        ),
        sa.CheckConstraint(
            "(product_id IS NULL AND product_snapshot IS NULL) OR "
            "(product_id IS NOT NULL AND product_snapshot IS NOT NULL)",
            name="ck_video_cards_snapshot_matches_product",
        ),
        sa.ForeignKeyConstraint(["blogger_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_video_cards_product_id", "video_cards", ["product_id"])
    op.create_index(
        "ix_video_cards_blogger_created", "video_cards", ["blogger_id", "created_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_video_cards_blogger_created", table_name="video_cards")
    op.drop_index("ix_video_cards_product_id", table_name="video_cards")
    op.drop_table("video_cards")
