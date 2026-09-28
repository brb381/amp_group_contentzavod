"""Require a reference for recorded payouts.

Revision ID: 0038_payout_reference
Revises: 0037_instagram_dzen_collection
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0038_payout_reference"
down_revision: Union[str, Sequence[str], None] = "0037_instagram_dzen_collection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE payout_requests "
        "SET payment_reference = 'legacy-missing:' || CAST(id AS text) "
        "WHERE status = 'paid' "
        "AND (payment_reference IS NULL OR length(trim(payment_reference)) = 0)"
    )
    op.create_check_constraint(
        "ck_payout_requests_paid_reference",
        "payout_requests",
        "status != 'paid' OR "
        "(payment_reference IS NOT NULL AND length(trim(payment_reference)) > 0)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_payout_requests_paid_reference",
        "payout_requests",
        type_="check",
    )
