"""Add append-only security journal.

Revision ID: 0006_security_events
Revises: 0005_password_reset
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0006_security_events"
down_revision: Union[str, Sequence[str], None] = "0005_password_reset"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "security_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_role", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=96), nullable=False),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=True),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("ip_address", sa.String(length=45), nullable=False),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("result IN ('success', 'failure', 'denied')", name="ck_security_events_result"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_security_events_occurred_at", "security_events", ["occurred_at"])
    op.create_index("ix_security_events_action", "security_events", ["action"])
    op.create_index("ix_security_events_request_id", "security_events", ["request_id"])
    op.create_index("ix_security_events_actor_time", "security_events", ["actor_user_id", "occurred_at"])
    op.create_index("ix_security_events_object", "security_events", ["object_type", "object_id"])
    op.execute(
        """
        CREATE FUNCTION reject_security_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'security_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER security_events_append_only
        BEFORE UPDATE OR DELETE ON security_events
        FOR EACH ROW EXECUTE FUNCTION reject_security_event_mutation()
        """
    )


def downgrade() -> None:
    op.drop_table("security_events")
    op.execute("DROP FUNCTION reject_security_event_mutation()")
