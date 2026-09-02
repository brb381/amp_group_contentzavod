"""Add social account moderation history and soft deletion.

Revision ID: 0004_social_account_moderation
Revises: 0003_creator_profiles
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004_social_account_moderation"
down_revision: Union[str, Sequence[str], None] = "0003_creator_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("social_accounts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_social_accounts_deleted_at", "social_accounts", ["deleted_at"])

    op.create_table(
        "social_account_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("social_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["social_account_id"], ["social_accounts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_social_account_history_account_id", "social_account_history", ["social_account_id"])
    op.create_index("ix_social_account_history_actor_user_id", "social_account_history", ["actor_user_id"])
    op.create_index("ix_social_account_history_event_type", "social_account_history", ["event_type"])


def downgrade() -> None:
    op.drop_table("social_account_history")
    op.drop_index("ix_social_accounts_deleted_at", table_name="social_accounts")
    op.drop_column("social_accounts", "deleted_at")
