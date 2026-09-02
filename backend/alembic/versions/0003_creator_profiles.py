"""Add creator profiles, social accounts, and moderation history.

Revision ID: 0003_creator_profiles
Revises: 0002_email_verification_outbox
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0003_creator_profiles"
down_revision: Union[str, Sequence[str], None] = "0002_email_verification_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

profile_status_enum = postgresql.ENUM(
    "draft",
    "submitted",
    "in_review",
    "approved",
    "rejected",
    "suspended",
    "blocked",
    "deleted",
    name="profilestatus",
    create_type=False,
)
recipient_status_enum = postgresql.ENUM(
    "individual", "self_employed", name="recipientstatus", create_type=False
)
social_account_status_enum = postgresql.ENUM(
    "pending", "approved", "rejected", name="socialaccountstatus", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    profile_status_enum.create(bind, checkfirst=True)
    recipient_status_enum.create(bind, checkfirst=True)
    social_account_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "creator_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("telegram", sa.String(length=128), nullable=True),
        sa.Column("city_country", sa.String(length=255), nullable=True),
        sa.Column("content_topics", sa.String(length=500), nullable=True),
        sa.Column("recipient_status", recipient_status_enum, nullable=True),
        sa.Column("status", profile_status_enum, nullable=False, server_default="draft"),
        sa.Column("moderation_reason", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_creator_profiles_user_id"),
    )
    op.create_index("ix_creator_profiles_user_id", "creator_profiles", ["user_id"])
    op.create_index("ix_creator_profiles_status_submitted_at", "creator_profiles", ["status", "submitted_at"])

    op.create_table(
        "social_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("status", social_account_status_enum, nullable=False, server_default="pending"),
        sa.Column("moderation_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "url", name="uq_social_accounts_user_url"),
    )
    op.create_index("ix_social_accounts_user_id", "social_accounts", ["user_id"])
    op.create_index("ix_social_accounts_status", "social_accounts", ["status"])

    op.create_table(
        "profile_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["profile_id"], ["creator_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_profile_history_profile_id", "profile_history", ["profile_id"])
    op.create_index("ix_profile_history_actor_user_id", "profile_history", ["actor_user_id"])
    op.create_index("ix_profile_history_event_type", "profile_history", ["event_type"])


def downgrade() -> None:
    op.drop_table("profile_history")
    op.drop_table("social_accounts")
    op.drop_table("creator_profiles")

    bind = op.get_bind()
    social_account_status_enum.drop(bind, checkfirst=True)
    recipient_status_enum.drop(bind, checkfirst=True)
    profile_status_enum.drop(bind, checkfirst=True)
