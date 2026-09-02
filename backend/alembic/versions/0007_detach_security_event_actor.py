"""Keep audit actor IDs as immutable snapshots.

Revision ID: 0007_detach_audit_actor
Revises: 0006_security_events
Create Date: 2026-08-11
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0007_detach_audit_actor"
down_revision: Union[str, Sequence[str], None] = "0006_security_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "security_events_actor_user_id_fkey",
        "security_events",
        type_="foreignkey",
    )


def downgrade() -> None:
    op.create_foreign_key(
        "security_events_actor_user_id_fkey",
        "security_events",
        "users",
        ["actor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
