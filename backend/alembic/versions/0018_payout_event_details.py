"""Preserve payout command details and immutable recorded facts.

Revision ID: 0018_payout_event_details
Revises: 0017_db_runtime_roles
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0018_payout_event_details"
down_revision: Union[str, Sequence[str], None] = "0017_db_runtime_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payout_event_details",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("payment_reference", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "comment IS NOT NULL OR rejection_reason IS NOT NULL OR "
            "payment_reference IS NOT NULL",
            name="ck_payout_event_details_not_empty",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["payout_events.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )

    if op.get_bind().dialect.name != "postgresql":
        return

    # Existing rows only expose the latest stored comment. Attach it to the last
    # event that recorded a comment; rejection reasons and payment references
    # are recoverable from their terminal request fields.
    op.execute(
        """
        INSERT INTO payout_event_details (event_id, rejection_reason)
        SELECT event.id, request.rejection_reason
          FROM payout_events AS event
          JOIN payout_requests AS request ON request.id = event.payout_request_id
         WHERE event.action = 'rejected' AND request.rejection_reason IS NOT NULL
        ON CONFLICT (event_id) DO UPDATE
            SET rejection_reason = EXCLUDED.rejection_reason
        """
    )
    op.execute(
        """
        INSERT INTO payout_event_details (event_id, payment_reference)
        SELECT event.id, request.payment_reference
          FROM payout_events AS event
          JOIN payout_requests AS request ON request.id = event.payout_request_id
         WHERE event.action = 'paid' AND request.payment_reference IS NOT NULL
        ON CONFLICT (event_id) DO UPDATE
            SET payment_reference = EXCLUDED.payment_reference
        """
    )
    op.execute(
        """
        INSERT INTO payout_event_details (event_id, comment)
        SELECT latest.event_id, request.manager_comment
          FROM payout_requests AS request
          JOIN LATERAL (
              SELECT event.id AS event_id
                FROM payout_events AS event
               WHERE event.payout_request_id = request.id
                 AND COALESCE(event.metadata ->> 'comment_recorded', 'false') = 'true'
               ORDER BY event.sequence_number DESC
               LIMIT 1
          ) AS latest ON TRUE
         WHERE request.manager_comment IS NOT NULL
        ON CONFLICT (event_id) DO UPDATE
            SET comment = EXCLUDED.comment
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payout_event_details_append_only
        BEFORE UPDATE OR DELETE ON payout_event_details
        FOR EACH ROW EXECUTE FUNCTION prevent_financial_history_mutation()
        """
    )
    op.execute("DROP FUNCTION prevent_payout_snapshot_mutation() CASCADE")
    op.execute(
        """
        CREATE FUNCTION prevent_payout_snapshot_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.request_number IS DISTINCT FROM OLD.request_number
                OR NEW.blogger_id IS DISTINCT FROM OLD.blogger_id
                OR NEW.amount_kopecks IS DISTINCT FROM OLD.amount_kopecks
                OR NEW.currency IS DISTINCT FROM OLD.currency
                OR NEW.recipient_full_name IS DISTINCT FROM OLD.recipient_full_name
                OR NEW.recipient_display_name IS DISTINCT FROM OLD.recipient_display_name
                OR NEW.recipient_type IS DISTINCT FROM OLD.recipient_type
                OR NEW.sbp_phone IS DISTINCT FROM OLD.sbp_phone
                OR NEW.bank_name IS DISTINCT FROM OLD.bank_name
                OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
            THEN
                RAISE EXCEPTION 'payout request snapshot is immutable';
            END IF;

            IF (OLD.review_started_at IS NOT NULL AND
                    (NEW.review_started_at, NEW.reviewer_user_id)
                    IS DISTINCT FROM (OLD.review_started_at, OLD.reviewer_user_id))
                OR (OLD.requisites_verified_at IS NOT NULL AND
                    (NEW.requisites_verified_at, NEW.requisites_verified_by_user_id)
                    IS DISTINCT FROM
                    (OLD.requisites_verified_at, OLD.requisites_verified_by_user_id))
                OR (OLD.self_employment_verified_at IS NOT NULL AND
                    (NEW.self_employment_verified_at,
                     NEW.self_employment_verified_by_user_id)
                    IS DISTINCT FROM
                    (OLD.self_employment_verified_at,
                     OLD.self_employment_verified_by_user_id))
                OR (OLD.approved_at IS NOT NULL AND
                    (NEW.approved_at, NEW.approved_by_user_id, NEW.payment_due_date)
                    IS DISTINCT FROM
                    (OLD.approved_at, OLD.approved_by_user_id, OLD.payment_due_date))
                OR (OLD.paid_on IS NOT NULL AND
                    (NEW.paid_on, NEW.paid_recorded_at, NEW.paid_by_user_id,
                     NEW.payment_reference)
                    IS DISTINCT FROM
                    (OLD.paid_on, OLD.paid_recorded_at, OLD.paid_by_user_id,
                     OLD.payment_reference))
                OR (OLD.rejected_at IS NOT NULL AND
                    (NEW.rejected_at, NEW.rejected_by_user_id, NEW.rejection_reason)
                    IS DISTINCT FROM
                    (OLD.rejected_at, OLD.rejected_by_user_id, OLD.rejection_reason))
                OR (OLD.receipt_received_on IS NOT NULL AND
                    (NEW.receipt_received_on, NEW.receipt_recorded_at,
                     NEW.receipt_received_by_user_id)
                    IS DISTINCT FROM
                    (OLD.receipt_received_on, OLD.receipt_recorded_at,
                     OLD.receipt_received_by_user_id))
                OR (OLD.status IN ('paid', 'rejected') AND
                    NEW.manager_comment IS DISTINCT FROM OLD.manager_comment)
            THEN
                RAISE EXCEPTION 'recorded payout facts are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payout_requests_snapshot_immutable
        BEFORE UPDATE ON payout_requests
        FOR EACH ROW EXECUTE FUNCTION prevent_payout_snapshot_mutation()
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Migration 0018_payout_event_details is irreversible because it preserves "
        "financial command history not represented by the previous schema."
    )
