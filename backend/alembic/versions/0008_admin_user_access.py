"""Add administrative user access state.

Revision ID: 0008_admin_user_access
Revises: 0007_detach_audit_actor
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0008_admin_user_access"
down_revision: Union[str, Sequence[str], None] = "0007_detach_audit_actor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

account_status = postgresql.ENUM(
    "email_pending",
    "active",
    "suspended",
    "blocked",
    "deleted",
    name="accountstatus",
    create_type=False,
)


def upgrade() -> None:
    op.add_column("users", sa.Column("status_before_block", account_status, nullable=True))
    op.add_column("users", sa.Column("status_reason", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.execute(
        """
        UPDATE users
        SET status_before_block = CASE
            WHEN email_verified_at IS NULL THEN 'email_pending'::accountstatus
            ELSE 'active'::accountstatus
        END
        WHERE status = 'blocked'::accountstatus
          AND status_before_block IS NULL
        """
    )
    op.create_index("ix_users_role_status", "users", ["role", "status"])


def downgrade() -> None:
    op.drop_index("ix_users_role_status", table_name="users")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "status_changed_at")
    op.drop_column("users", "status_reason")
    op.drop_column("users", "status_before_block")
