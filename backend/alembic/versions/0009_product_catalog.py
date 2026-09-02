"""Add product catalog.

Revision ID: 0009_product_catalog
Revises: 0008_admin_user_access
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0009_product_catalog"
down_revision: Union[str, Sequence[str], None] = "0008_admin_user_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

product_brand = postgresql.ENUM("AMP", "AirTone", "CrioLight", name="productbrand", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    product_brand.create(bind, checkfirst=True)
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("brand", product_brand, nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("publication_name", sa.String(length=500), nullable=False),
        sa.Column("sku", sa.String(length=128), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_sku", sa.String(length=128), nullable=False),
        sa.Column("required_hashtags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hint", sa.Text(), nullable=True),
        sa.Column("marketplace_links", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("brand", "normalized_name", name="uq_products_brand_normalized_name"),
        sa.UniqueConstraint("brand", "normalized_sku", name="uq_products_brand_normalized_sku"),
    )
    op.create_index("ix_products_active_brand_name", "products", ["is_active", "brand", "normalized_name"])


def downgrade() -> None:
    op.drop_index("ix_products_active_brand_name", table_name="products")
    op.drop_table("products")
    product_brand.drop(op.get_bind(), checkfirst=True)
