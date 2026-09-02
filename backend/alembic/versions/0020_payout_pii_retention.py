"""Add controlled irreversible payout PII retention.

Revision ID: 0020_payout_pii_retention
Revises: 0019_async_exports
Create Date: 2026-08-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0020_payout_pii_retention"
down_revision: Union[str, Sequence[str], None] = "0019_async_exports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payout_details",
        sa.Column("pii_anonymized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payout_requests",
        sa.Column("pii_anonymized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "payout_event_details",
        sa.Column("pii_anonymized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_payout_requests_pii_retention",
        "payout_requests",
        ["pii_anonymized_at", "blogger_id"],
    )

    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        """
        DO $validate_retention_role$
        DECLARE role_data record;
        BEGIN
            SELECT * INTO role_data FROM pg_roles WHERE rolname = 'amp_retention_worker';
            IF role_data IS NULL OR NOT role_data.rolcanlogin OR role_data.rolsuper
                OR role_data.rolcreatedb OR role_data.rolcreaterole
                OR role_data.rolreplication OR role_data.rolbypassrls
                OR EXISTS (
                    SELECT 1 FROM pg_auth_members WHERE member = role_data.oid
                )
            THEN
                RAISE EXCEPTION 'Missing or unsafe amp_retention_worker role';
            END IF;
        END
        $validate_retention_role$;
        """
    )
    op.execute(
        """
        DO $grant_retention_connect$
        BEGIN
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO amp_retention_worker', current_database()
            );
        END
        $grant_retention_connect$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO amp_retention_worker")
    op.execute(
        "GRANT SELECT ON TABLE users, payout_events TO amp_retention_worker"
    )
    op.execute(
        "GRANT SELECT, UPDATE ON TABLE payout_details, payout_requests, "
        "payout_event_details TO amp_retention_worker"
    )
    op.execute("GRANT INSERT ON TABLE security_events TO amp_retention_worker")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES "
        "ON TABLES FROM amp_retention_worker"
    )

    op.execute("DROP TRIGGER trg_payout_requests_snapshot_immutable ON payout_requests")
    op.execute("DROP FUNCTION prevent_payout_snapshot_mutation()")
    op.execute(
        """
        CREATE FUNCTION prevent_payout_snapshot_mutation()
        RETURNS trigger AS $$
        DECLARE anonymizing boolean;
        BEGIN
            anonymizing := OLD.pii_anonymized_at IS NULL
                AND NEW.pii_anonymized_at IS NOT NULL
                AND OLD.status IN ('paid', 'rejected')
                AND NEW.recipient_full_name = '[anonymized]'
                AND NEW.recipient_display_name = '[anonymized]'
                AND NEW.sbp_phone = '[anonymized]'
                AND NEW.bank_name IS NULL
                AND NEW.manager_comment IS NULL
                AND (OLD.rejection_reason IS NULL
                     OR NEW.rejection_reason = '[anonymized]');

            IF NEW.request_number IS DISTINCT FROM OLD.request_number
                OR NEW.blogger_id IS DISTINCT FROM OLD.blogger_id
                OR NEW.amount_kopecks IS DISTINCT FROM OLD.amount_kopecks
                OR NEW.currency IS DISTINCT FROM OLD.currency
                OR NEW.recipient_type IS DISTINCT FROM OLD.recipient_type
                OR NEW.requested_at IS DISTINCT FROM OLD.requested_at
                OR ((NEW.recipient_full_name IS DISTINCT FROM OLD.recipient_full_name
                     OR NEW.recipient_display_name IS DISTINCT FROM OLD.recipient_display_name
                     OR NEW.sbp_phone IS DISTINCT FROM OLD.sbp_phone
                     OR NEW.bank_name IS DISTINCT FROM OLD.bank_name)
                    AND NOT anonymizing)
                OR (OLD.pii_anonymized_at IS NOT NULL AND
                    NEW.pii_anonymized_at IS DISTINCT FROM OLD.pii_anonymized_at)
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
                    (NEW.rejected_at, NEW.rejected_by_user_id)
                    IS DISTINCT FROM (OLD.rejected_at, OLD.rejected_by_user_id))
                OR (OLD.rejection_reason IS NOT NULL
                    AND NEW.rejection_reason IS DISTINCT FROM OLD.rejection_reason
                    AND NOT anonymizing)
                OR (OLD.receipt_received_on IS NOT NULL AND
                    (NEW.receipt_received_on, NEW.receipt_recorded_at,
                     NEW.receipt_received_by_user_id)
                    IS DISTINCT FROM
                    (OLD.receipt_received_on, OLD.receipt_recorded_at,
                     OLD.receipt_received_by_user_id))
                OR (OLD.status IN ('paid', 'rejected')
                    AND NEW.manager_comment IS DISTINCT FROM OLD.manager_comment
                    AND NOT anonymizing)
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

    op.execute(
        """
        CREATE FUNCTION prevent_anonymized_payout_details_rewrite()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.pii_anonymized_at IS NOT NULL THEN
                RAISE EXCEPTION 'anonymized payout details are immutable';
            END IF;
            IF NEW.pii_anonymized_at IS NOT NULL AND
                (NEW.sbp_phone <> '[anonymized]' OR NEW.bank_name IS NOT NULL)
            THEN
                RAISE EXCEPTION 'invalid payout details anonymization';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payout_details_anonymization
        BEFORE UPDATE ON payout_details
        FOR EACH ROW EXECUTE FUNCTION prevent_anonymized_payout_details_rewrite()
        """
    )

    op.execute(
        "DROP TRIGGER trg_payout_event_details_append_only ON payout_event_details"
    )
    op.execute(
        """
        CREATE FUNCTION prevent_payout_event_detail_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.pii_anonymized_at IS NOT NULL
                OR NEW.event_id IS DISTINCT FROM OLD.event_id
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR NEW.payment_reference IS DISTINCT FROM OLD.payment_reference
                OR NEW.pii_anonymized_at IS NULL
                OR (NEW.comment IS NOT NULL AND NEW.comment <> '[anonymized]')
                OR (NEW.rejection_reason IS NOT NULL
                    AND NEW.rejection_reason <> '[anonymized]')
            THEN
                RAISE EXCEPTION 'payout event details are append-only';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payout_event_details_append_only
        BEFORE UPDATE ON payout_event_details
        FOR EACH ROW EXECUTE FUNCTION prevent_payout_event_detail_mutation()
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Migration 0020_payout_pii_retention is irreversible because anonymized "
        "personal data cannot be reconstructed."
    )
