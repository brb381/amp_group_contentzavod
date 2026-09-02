"""Add payout requests, immutable events, and wallet transfers.

Revision ID: 0016_payouts
Revises: 0015_billing
Create Date: 2026-08-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0016_payouts"
down_revision: Union[str, Sequence[str], None] = "0015_billing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ACTIVE_PAYOUT_STATUS_SQL = "status IN ('requested', 'under_review', 'approved')"


def upgrade() -> None:
    op.drop_constraint(
        "ck_balance_ledger_operation_type", "balance_ledger", type_="check"
    )
    op.create_check_constraint(
        "ck_balance_ledger_operation_type",
        "balance_ledger",
        "operation_type IN ('period_accrual', 'period_correction', "
        "'payout_reserved', 'payout_released', 'payout_paid')",
    )
    op.create_check_constraint(
        "ck_balance_ledger_payout_transfer",
        "balance_ledger",
        "operation_type NOT IN ('payout_reserved', 'payout_released', 'payout_paid') OR "
        "(operation_type = 'payout_reserved' AND available_delta_kopecks < 0 AND "
        "reserved_delta_kopecks = -available_delta_kopecks AND paid_delta_kopecks = 0) OR "
        "(operation_type = 'payout_released' AND available_delta_kopecks > 0 AND "
        "reserved_delta_kopecks = -available_delta_kopecks AND paid_delta_kopecks = 0) OR "
        "(operation_type = 'payout_paid' AND available_delta_kopecks = 0 AND "
        "reserved_delta_kopecks < 0 AND paid_delta_kopecks = -reserved_delta_kopecks)",
    )

    op.create_table(
        "payout_details",
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sbp_phone", sa.String(32), nullable=False),
        sa.Column("bank_name", sa.String(255), nullable=True),
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
        sa.CheckConstraint(
            "length(trim(sbp_phone)) > 0", name="ck_payout_details_phone_required"
        ),
        sa.CheckConstraint(
            "bank_name IS NULL OR length(trim(bank_name)) > 0",
            name="ck_payout_details_bank_nonempty",
        ),
        sa.ForeignKeyConstraint(["blogger_id"], ["users.id"], ondelete="RESTRICT"),
    )

    op.create_table(
        "payout_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_number", sa.String(40), nullable=False),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("status", sa.String(20), nullable=False, server_default="requested"),
        sa.Column("recipient_full_name", sa.String(255), nullable=False),
        sa.Column("recipient_display_name", sa.String(255), nullable=False),
        sa.Column("recipient_type", sa.String(20), nullable=False),
        sa.Column("sbp_phone", sa.String(32), nullable=False),
        sa.Column("bank_name", sa.String(255), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("review_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requisites_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "requisites_verified_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "self_employment_verified_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "self_employment_verified_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payment_due_date", sa.Date(), nullable=True),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column("paid_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payment_reference", sa.String(255), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("manager_comment", sa.Text(), nullable=True),
        sa.Column("receipt_due_date", sa.Date(), nullable=True),
        sa.Column("receipt_received_on", sa.Date(), nullable=True),
        sa.Column("receipt_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "receipt_received_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
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
        sa.CheckConstraint(
            "amount_kopecks > 0", name="ck_payout_requests_amount_positive"
        ),
        sa.CheckConstraint("currency = 'RUB'", name="ck_payout_requests_currency_rub"),
        sa.CheckConstraint(
            "status IN ('requested', 'under_review', 'approved', 'paid', 'rejected')",
            name="ck_payout_requests_status",
        ),
        sa.CheckConstraint(
            "recipient_type IN ('individual', 'self_employed')",
            name="ck_payout_requests_recipient_type",
        ),
        sa.CheckConstraint(
            "length(trim(request_number)) > 0 AND "
            "length(trim(recipient_full_name)) > 0 AND "
            "length(trim(recipient_display_name)) > 0 AND "
            "length(trim(sbp_phone)) > 0",
            name="ck_payout_requests_snapshot_required",
        ),
        sa.CheckConstraint(
            "((review_started_at IS NULL AND reviewer_user_id IS NULL) OR "
            "(review_started_at IS NOT NULL AND reviewer_user_id IS NOT NULL))",
            name="ck_payout_requests_review_pair",
        ),
        sa.CheckConstraint(
            "status NOT IN ('under_review', 'approved', 'paid') OR "
            "(review_started_at IS NOT NULL AND reviewer_user_id IS NOT NULL)",
            name="ck_payout_requests_review_required",
        ),
        sa.CheckConstraint(
            "((requisites_verified_at IS NULL AND requisites_verified_by_user_id IS NULL) OR "
            "(requisites_verified_at IS NOT NULL AND requisites_verified_by_user_id IS NOT NULL))",
            name="ck_payout_requests_requisites_pair",
        ),
        sa.CheckConstraint(
            "((self_employment_verified_at IS NULL AND "
            "self_employment_verified_by_user_id IS NULL) OR "
            "(self_employment_verified_at IS NOT NULL AND "
            "self_employment_verified_by_user_id IS NOT NULL))",
            name="ck_payout_requests_self_employment_pair",
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'paid') OR "
            "(approved_at IS NOT NULL AND approved_by_user_id IS NOT NULL AND "
            "payment_due_date IS NOT NULL AND requisites_verified_at IS NOT NULL)",
            name="ck_payout_requests_approval_required",
        ),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'paid') OR recipient_type != 'self_employed' OR "
            "self_employment_verified_at IS NOT NULL",
            name="ck_payout_requests_self_employment_approval",
        ),
        sa.CheckConstraint(
            "((paid_on IS NULL AND paid_recorded_at IS NULL AND paid_by_user_id IS NULL) OR "
            "(paid_on IS NOT NULL AND paid_recorded_at IS NOT NULL AND "
            "paid_by_user_id IS NOT NULL))",
            name="ck_payout_requests_paid_pair",
        ),
        sa.CheckConstraint(
            "(status = 'paid' AND paid_on IS NOT NULL) OR "
            "(status != 'paid' AND paid_on IS NULL)",
            name="ck_payout_requests_paid_status",
        ),
        sa.CheckConstraint(
            "((rejected_at IS NULL AND rejected_by_user_id IS NULL AND rejection_reason IS NULL) "
            "OR (rejected_at IS NOT NULL AND rejected_by_user_id IS NOT NULL AND "
            "rejection_reason IS NOT NULL AND length(trim(rejection_reason)) > 0))",
            name="ck_payout_requests_rejection_pair",
        ),
        sa.CheckConstraint(
            "(status = 'rejected' AND rejected_at IS NOT NULL) OR "
            "(status != 'rejected' AND rejected_at IS NULL)",
            name="ck_payout_requests_rejected_status",
        ),
        sa.CheckConstraint(
            "receipt_due_date IS NULL OR "
            "(status = 'paid' AND recipient_type = 'self_employed')",
            name="ck_payout_requests_receipt_due_scope",
        ),
        sa.CheckConstraint(
            "status != 'paid' OR recipient_type != 'self_employed' OR "
            "receipt_due_date IS NOT NULL",
            name="ck_payout_requests_receipt_due_required",
        ),
        sa.CheckConstraint(
            "((receipt_received_on IS NULL AND receipt_recorded_at IS NULL AND "
            "receipt_received_by_user_id IS NULL) OR "
            "(receipt_received_on IS NOT NULL AND receipt_recorded_at IS NOT NULL AND "
            "receipt_received_by_user_id IS NOT NULL AND receipt_due_date IS NOT NULL AND "
            "status = 'paid' AND recipient_type = 'self_employed'))",
            name="ck_payout_requests_receipt_received_pair",
        ),
        sa.ForeignKeyConstraint(["blogger_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["requisites_verified_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["self_employment_verified_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["paid_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["rejected_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["receipt_received_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("request_number", name="uq_payout_requests_request_number"),
    )
    op.create_index(
        "uq_payout_requests_active_blogger",
        "payout_requests",
        ["blogger_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_PAYOUT_STATUS_SQL),
    )
    op.create_index(
        "ix_payout_requests_blogger_requested",
        "payout_requests",
        ["blogger_id", "requested_at"],
    )
    op.create_index(
        "ix_payout_requests_status_requested",
        "payout_requests",
        ["status", "requested_at"],
    )
    op.create_index(
        "ix_payout_requests_payment_due",
        "payout_requests",
        ["status", "payment_due_date"],
    )
    op.create_index(
        "ix_payout_requests_receipt_due",
        "payout_requests",
        ["recipient_type", "status", "receipt_due_date"],
    )

    op.create_table(
        "payout_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("payout_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "sequence_number >= 1", name="ck_payout_events_sequence_positive"
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64", name="ck_payout_events_payload_hash"
        ),
        sa.CheckConstraint(
            "action IN ('requested', 'review_started', 'approved', 'rejected', "
            "'paid', 'receipt_received')",
            name="ck_payout_events_action",
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('requested', 'under_review', 'approved', 'paid', 'rejected')",
            name="ck_payout_events_from_status",
        ),
        sa.CheckConstraint(
            "to_status IN ('requested', 'under_review', 'approved', 'paid', 'rejected')",
            name="ck_payout_events_to_status",
        ),
        sa.ForeignKeyConstraint(
            ["payout_request_id"], ["payout_requests.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_payout_events_idempotency_key"),
        sa.UniqueConstraint(
            "payout_request_id",
            "sequence_number",
            name="uq_payout_events_request_sequence",
        ),
    )
    op.create_index(
        "ix_payout_events_request_created",
        "payout_events",
        ["payout_request_id", "created_at"],
    )
    op.create_index(
        "ix_payout_events_actor_created",
        "payout_events",
        ["actor_user_id", "created_at"],
    )

    op.execute(
        """
        CREATE TRIGGER trg_payout_events_append_only
        BEFORE UPDATE OR DELETE ON payout_events
        FOR EACH ROW EXECUTE FUNCTION prevent_financial_history_mutation()
        """
    )
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
        "Migration 0016_payouts is irreversible: payout requests and their ledger "
        "entries are financial records. Restore from backup or roll forward instead."
    )
