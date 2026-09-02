"""Add calculation, accrual, and creator balance data model.

Revision ID: 0015_billing
Revises: 0014_view_readings
Create Date: 2026-08-21
"""

import uuid
from datetime import date
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0015_billing"
down_revision: Union[str, Sequence[str], None] = "0014_view_readings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INITIAL_RATE_VERSION_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def upgrade() -> None:
    billing_control = op.create_table(
        "billing_control",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("id = 1", name="ck_billing_control_singleton"),
    )
    op.bulk_insert(billing_control, [{"id": 1}])

    rate_versions = op.create_table(
        "rate_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rate_kopecks_per_view", sa.BigInteger(), nullable=False),
        sa.Column("effective_from_period", sa.Date(), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "rate_kopecks_per_view > 0", name="ck_rate_versions_rate_positive"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "effective_from_period", name="uq_rate_versions_effective_from_period"
        ),
    )
    op.bulk_insert(
        rate_versions,
        [
            {
                "id": INITIAL_RATE_VERSION_ID,
                "rate_kopecks_per_view": 5,
                "effective_from_period": date(1970, 1, 1),
                "created_by_user_id": None,
                "reason": "Initial rate: 5 kopecks per eligible view",
            }
        ],
    )

    op.create_table(
        "calculation_periods",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("rate_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_revision", sa.BigInteger(), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("total_views", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "total_amount_kopecks", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_adjustment_kopecks", sa.BigInteger(), nullable=False, server_default="0"
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
            "status IN ('pending', 'preliminary', 'confirmed')",
            name="ck_calculation_periods_status",
        ),
        sa.CheckConstraint(
            "input_revision IS NULL OR input_revision >= 0",
            name="ck_calculation_periods_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "total_views >= 0", name="ck_calculation_periods_views_nonnegative"
        ),
        sa.CheckConstraint(
            "total_amount_kopecks >= 0",
            name="ck_calculation_periods_amount_nonnegative",
        ),
        sa.CheckConstraint(
            "total_amount_kopecks + total_adjustment_kopecks >= 0",
            name="ck_calculation_periods_payable_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["rate_version_id"], ["rate_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("period", name="uq_calculation_periods_period"),
    )
    op.create_index(
        "ix_calculation_periods_status_period",
        "calculation_periods",
        ["status", "period"],
    )

    op.create_table(
        "calculation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
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
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')",
            name="ck_calculation_jobs_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_calculation_jobs_attempt_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["period_id"], ["calculation_periods.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("period_id", name="uq_calculation_jobs_period_id"),
        sa.UniqueConstraint("dispatch_id", name="uq_calculation_jobs_dispatch_id"),
    )
    op.create_index(
        "ix_calculation_jobs_due",
        "calculation_jobs",
        ["state", "available_at", "created_at"],
    )

    op.add_column(
        "view_readings",
        sa.Column("financial_locked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "view_readings",
        sa.Column(
            "financial_locked_by_period_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_view_readings_financial_locked_by_period_id",
        "view_readings",
        "calculation_periods",
        ["financial_locked_by_period_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    reading_revision = op.create_table(
        "reading_dataset_revision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("closed_through_period", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "id = 1", name="ck_reading_dataset_revision_singleton"
        ),
        sa.CheckConstraint(
            "revision >= 0", name="ck_reading_dataset_revision_nonnegative"
        ),
    )
    op.bulk_insert(
        reading_revision,
        [{"id": 1, "revision": 0, "closed_through_period": None}],
    )

    op.create_table(
        "publication_accruals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_reading_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("previous_value", sa.BigInteger(), nullable=True),
        sa.Column(
            "previous_reading_updated_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("current_reading_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_value", sa.BigInteger(), nullable=True),
        sa.Column(
            "current_reading_updated_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("eligible_views", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rate_kopecks_per_view", sa.BigInteger(), nullable=False),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("adjustment_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("exclusion_reason", sa.String(32), nullable=True),
        sa.Column(
            "risk_flags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "previous_value IS NULL OR previous_value >= 0",
            name="ck_publication_accrual_previous_nonnegative",
        ),
        sa.CheckConstraint(
            "current_value IS NULL OR current_value >= 0",
            name="ck_publication_accrual_current_nonnegative",
        ),
        sa.CheckConstraint(
            "eligible_views >= 0", name="ck_publication_accrual_views_nonnegative"
        ),
        sa.CheckConstraint(
            "rate_kopecks_per_view > 0",
            name="ck_publication_accrual_rate_positive",
        ),
        sa.CheckConstraint(
            "amount_kopecks >= 0", name="ck_publication_accrual_amount_nonnegative"
        ),
        sa.CheckConstraint(
            "amount_kopecks + adjustment_kopecks >= 0",
            name="ck_publication_accrual_payable_nonnegative",
        ),
        sa.CheckConstraint(
            "exclusion_reason IS NULL OR exclusion_reason IN ('missing_current_reading', 'baseline_only')",
            name="ck_publication_accrual_exclusion_reason",
        ),
        sa.ForeignKeyConstraint(
            ["period_id"], ["calculation_periods.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"], ["publications.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["blogger_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["previous_reading_id"], ["view_readings.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["current_reading_id"], ["view_readings.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "period_id",
            "publication_id",
            name="uq_publication_accrual_period_publication",
        ),
    )
    op.create_index(
        "ix_publication_accruals_period_blogger",
        "publication_accruals",
        ["period_id", "blogger_id"],
    )
    op.create_index(
        "ix_publication_accruals_publication_period",
        "publication_accruals",
        ["publication_id", "period_id"],
    )

    op.create_table(
        "creator_period_totals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("eligible_views", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("adjustment_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("publication_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "eligible_views >= 0", name="ck_creator_period_total_views_nonnegative"
        ),
        sa.CheckConstraint(
            "amount_kopecks >= 0", name="ck_creator_period_total_amount_nonnegative"
        ),
        sa.CheckConstraint(
            "amount_kopecks + adjustment_kopecks >= 0",
            name="ck_creator_period_total_payable_nonnegative",
        ),
        sa.CheckConstraint(
            "publication_count >= 0",
            name="ck_creator_period_total_publications_nonnegative",
        ),
        sa.CheckConstraint(
            "risk_count >= 0", name="ck_creator_period_total_risks_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["period_id"], ["calculation_periods.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["blogger_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "period_id", "blogger_id", name="uq_creator_period_total_period_blogger"
        ),
    )
    op.create_index(
        "ix_creator_period_totals_blogger_period",
        "creator_period_totals",
        ["blogger_id", "period_id"],
    )

    op.create_table(
        "creator_balances",
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("available_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("paid_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "reserved_kopecks >= 0", name="ck_creator_balances_reserved_nonnegative"
        ),
        sa.CheckConstraint(
            "paid_kopecks >= 0", name="ck_creator_balances_paid_nonnegative"
        ),
        sa.ForeignKeyConstraint(["blogger_id"], ["users.id"], ondelete="RESTRICT"),
    )

    op.create_table(
        "balance_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_type", sa.String(32), nullable=False),
        sa.Column(
            "available_delta_kopecks", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "reserved_delta_kopecks", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("paid_delta_kopecks", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reference_type", sa.String(64), nullable=False),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "operation_type IN ('period_accrual', 'period_correction')",
            name="ck_balance_ledger_operation_type",
        ),
        sa.ForeignKeyConstraint(["blogger_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_balance_ledger_idempotency_key"
        ),
    )
    op.create_index(
        "ix_balance_ledger_blogger_created",
        "balance_ledger",
        ["blogger_id", "created_at"],
    )
    op.create_index(
        "ix_balance_ledger_reference",
        "balance_ledger",
        ["reference_type", "reference_id"],
    )

    op.create_table(
        "accrual_corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("accrual_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("old_current_value", sa.BigInteger(), nullable=True),
        sa.Column("new_current_value", sa.BigInteger(), nullable=False),
        sa.Column("old_amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("new_amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("delta_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "old_current_value IS NULL OR old_current_value >= 0", name="ck_accrual_correction_old_value_nonnegative"
        ),
        sa.CheckConstraint(
            "new_current_value >= 0", name="ck_accrual_correction_new_value_nonnegative"
        ),
        sa.CheckConstraint(
            "old_amount_kopecks >= 0",
            name="ck_accrual_correction_old_amount_nonnegative",
        ),
        sa.CheckConstraint(
            "new_amount_kopecks >= 0",
            name="ck_accrual_correction_new_amount_nonnegative",
        ),
        sa.CheckConstraint(
            "sequence_number >= 1",
            name="ck_accrual_correction_sequence_positive",
        ),
        sa.ForeignKeyConstraint(
            ["accrual_id"], ["publication_accruals.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_accrual_corrections_idempotency_key"
        ),
        sa.UniqueConstraint(
            "accrual_id",
            "sequence_number",
            name="uq_accrual_correction_sequence",
        ),
    )
    op.create_index(
        "ix_accrual_corrections_accrual_created",
        "accrual_corrections",
        ["accrual_id", "created_at"],
    )

    op.execute(
        """
        CREATE FUNCTION prevent_financial_history_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in ("rate_versions", "balance_ledger", "accrual_corrections"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION prevent_financial_history_mutation()
            """
        )


def downgrade() -> None:
    for table_name in ("rate_versions", "balance_ledger", "accrual_corrections"):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}"
        )
    op.execute("DROP FUNCTION IF EXISTS prevent_financial_history_mutation()")

    op.drop_table("accrual_corrections")
    op.drop_table("balance_ledger")
    op.drop_table("creator_balances")
    op.drop_table("creator_period_totals")
    op.drop_table("publication_accruals")
    op.drop_table("reading_dataset_revision")
    op.drop_constraint(
        "fk_view_readings_financial_locked_by_period_id",
        "view_readings",
        type_="foreignkey",
    )
    op.drop_column("view_readings", "financial_locked_by_period_id")
    op.drop_column("view_readings", "financial_locked_at")
    op.drop_table("calculation_jobs")
    op.drop_table("calculation_periods")
    op.drop_table("rate_versions")
    op.drop_table("billing_control")
