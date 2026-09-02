"""Add publication workflow.

Revision ID: 0011_publications
Revises: 0010_video_cards
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0011_publications"
down_revision: Union[str, Sequence[str], None] = "0010_video_cards"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

publication_status = postgresql.ENUM(
    "draft",
    "pending_review",
    "changes_required",
    "approved",
    "rejected",
    "inactive",
    "re_review_required",
    name="publicationstatus",
    create_type=False,
)
publication_parse_status = postgresql.ENUM(
    "parsed",
    "pending",
    "manual_review",
    name="publicationparsestatus",
    create_type=False,
)
publication_availability = postgresql.ENUM(
    "unknown",
    "available",
    "unavailable",
    name="publicationavailability",
    create_type=False,
)
platform_check = "platform IN ('youtube', 'vk', 'tiktok', 'instagram', 'dzen', 'rutube')"


def upgrade() -> None:
    bind = op.get_bind()
    publication_status.create(bind, checkfirst=True)
    publication_parse_status.create(bind, checkfirst=True)
    publication_availability.create(bind, checkfirst=True)
    op.create_check_constraint(
        "ck_social_accounts_supported_platform",
        "social_accounts",
        platform_check,
    )
    op.create_table(
        "publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("video_card_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("social_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=False),
        sa.Column("submitted_url", sa.String(length=2048), nullable=False),
        sa.Column("normalized_url", sa.String(length=2048), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("status", publication_status, nullable=False, server_default="draft"),
        sa.Column("parse_status", publication_parse_status, nullable=False),
        sa.Column(
            "availability",
            publication_availability,
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("moderation_reason", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(platform_check, name="ck_publications_supported_platform"),
        sa.ForeignKeyConstraint(["video_card_id"], ["video_cards.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["social_account_id"], ["social_accounts.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("normalized_url", name="uq_publications_normalized_url"),
        sa.UniqueConstraint(
            "platform", "external_id", name="uq_publications_platform_external_id"
        ),
    )
    op.create_index(
        "ix_publications_card_created",
        "publications",
        ["video_card_id", "created_at", "id"],
    )
    op.create_index(
        "ix_publications_status_submitted", "publications", ["status", "submitted_at"]
    )
    op.create_index(
        "ix_publications_social_account_id", "publications", ["social_account_id"]
    )
    op.create_table(
        "publication_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "changes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"], ["publications.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_publication_history_publication_created",
        "publication_history",
        ["publication_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publication_history_publication_created", table_name="publication_history"
    )
    op.drop_table("publication_history")
    op.drop_index("ix_publications_social_account_id", table_name="publications")
    op.drop_index("ix_publications_status_submitted", table_name="publications")
    op.drop_index("ix_publications_card_created", table_name="publications")
    op.drop_table("publications")
    op.drop_constraint(
        "ck_social_accounts_supported_platform", "social_accounts", type_="check"
    )
    publication_availability.drop(op.get_bind(), checkfirst=True)
    publication_parse_status.drop(op.get_bind(), checkfirst=True)
    publication_status.drop(op.get_bind(), checkfirst=True)
