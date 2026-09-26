"""Add administrator-managed program settings.

Revision ID: 0031_program_settings
Revises: 0030_publication_operations
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0031_program_settings"
down_revision: Union[str, Sequence[str], None] = "0030_publication_operations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "program_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("program_name", sa.String(length=200), nullable=False),
        sa.Column("main_text", sa.Text(), nullable=True),
        sa.Column("primary_logo_url", sa.String(length=2048), nullable=True),
        sa.Column("secondary_logo_url", sa.String(length=2048), nullable=True),
        sa.Column("key_image_url", sa.String(length=2048), nullable=True),
        sa.Column("manager_name", sa.String(length=200), nullable=True),
        sa.Column("manager_email", sa.String(length=320), nullable=True),
        sa.Column("manager_phone", sa.String(length=50), nullable=True),
        sa.Column("manager_telegram_url", sa.String(length=2048), nullable=True),
        sa.Column("program_details", sa.Text(), nullable=True),
        sa.Column("service_signature", sa.Text(), nullable=True),
        sa.Column("suspicious_growth_threshold", sa.Integer(), nullable=False),
        sa.Column("random_review_percent", sa.Integer(), nullable=False),
        sa.Column("rejection_reasons", sa.JSON(), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_program_settings_singleton"),
        sa.CheckConstraint("suspicious_growth_threshold > 0", name="ck_program_settings_growth_positive"),
        sa.CheckConstraint("random_review_percent BETWEEN 0 AND 100", name="ck_program_settings_review_percent"),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(sa.table(
        "program_settings",
        sa.column("id", sa.Integer()), sa.column("program_name", sa.String()),
        sa.column("suspicious_growth_threshold", sa.Integer()),
        sa.column("random_review_percent", sa.Integer()), sa.column("rejection_reasons", sa.JSON()),
    ), [{"id": 1, "program_name": "AMP Content Factory", "suspicious_growth_threshold": 500000, "random_review_percent": 10, "rejection_reasons": []}])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("GRANT SELECT, INSERT, UPDATE ON program_settings TO amp_api")
        op.execute("GRANT SELECT ON program_settings TO amp_youtube_worker")
        op.execute("GRANT SELECT ON program_settings TO amp_tiktok_worker")
        op.execute("GRANT SELECT ON program_settings TO amp_vk_worker")


def downgrade() -> None:
    op.drop_table("program_settings")