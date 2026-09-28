"""Grant monitor read-only access to financial reconciliation inputs.

Revision ID: 0039_finance_monitor
Revises: 0038_payout_reference
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0039_finance_monitor"
down_revision: str | Sequence[str] | None = "0038_payout_reference"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = "creator_balances, balance_ledger, payout_requests"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"GRANT SELECT ON TABLE {TABLES} TO amp_monitor")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"REVOKE SELECT ON TABLE {TABLES} FROM amp_monitor")
