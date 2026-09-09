"""Allow the isolated export worker to read report source tables.

Revision ID: 0025_general_exports
Revises: 0024_legal_documents
Create Date: 2026-09-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0025_general_exports"
down_revision: Union[str, Sequence[str], None] = "0024_legal_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SOURCE_TABLES = (
    "creator_profiles",
    "social_accounts",
    "social_account_history",
    "profile_history",
    "video_cards",
    "publications",
    "publication_history",
    "products",
    "view_readings",
    "view_reading_history",
    "calculation_periods",
    "publication_accruals",
    "support_tickets",
    "support_messages",
    "security_events",
)


def upgrade() -> None:
    op.add_column(
        "export_jobs",
        sa.Column("artifact_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    tables = ", ".join(SOURCE_TABLES)
    op.execute(f"GRANT SELECT ON TABLE {tables} TO amp_export_worker")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        tables = ", ".join(SOURCE_TABLES)
        op.execute(f"REVOKE SELECT ON TABLE {tables} FROM amp_export_worker")
    op.drop_column("export_jobs", "artifact_deleted_at")
