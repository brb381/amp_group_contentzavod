"""Add identity for each email dispatch attempt.

Revision ID: 0013_email_dispatch_identity
Revises: 0012_youtube_enrichment
Create Date: 2026-08-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0013_email_dispatch_identity"
down_revision: Union[str, Sequence[str], None] = "0012_youtube_enrichment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "outbox_events",
        sa.Column("dispatch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_outbox_events_dispatch_id", "outbox_events", ["dispatch_id"])


def downgrade() -> None:
    op.drop_index("ix_outbox_events_dispatch_id", table_name="outbox_events")
    op.drop_column("outbox_events", "dispatch_id")
