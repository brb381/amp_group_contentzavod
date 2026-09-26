"""Grant the scheduler the write privilege required for its control-row lock.

Revision ID: 0027_scheduler_lock_privileges
Revises: 0026_operations_monitor
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0027_scheduler_lock_privileges"
down_revision: Union[str, Sequence[str], None] = "0026_operations_monitor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("GRANT UPDATE ON TABLE billing_control TO amp_scheduler")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("REVOKE UPDATE ON TABLE billing_control FROM amp_scheduler")
