"""Add creator inactivity lifecycle and recovery decisions.

Revision ID: 0022_account_lifecycle
Revises: 0021_notifications_support
Create Date: 2026-08-30
"""

import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0022_account_lifecycle"
down_revision: Union[str, Sequence[str], None] = "0021_notifications_support"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TEMPLATES = {
    "account_suspension_warning": (
        "Account inactivity warning",
        "Your account will be suspended in $days days on $deadline",
        ["days", "deadline"],
    ),
    "account_suspended": (
        "Account suspended",
        "Your account was suspended for inactivity. Submit a recovery ticket to continue",
        [],
    ),
    "account_block_warning": (
        "Account blocking warning",
        "Your suspended account will be blocked in $days days on $deadline",
        ["days", "deadline"],
    ),
    "account_fully_blocked": (
        "Account blocked",
        "Your account was blocked. Unclaimed balance: $balance_kopecks kopecks",
        ["balance_kopecks"],
    ),
    "account_recovery_approved": (
        "Account recovery approved",
        "Your account is active again. Publications require a new moderation review",
        [],
    ),
    "account_recovery_rejected": (
        "Account recovery rejected",
        "Your recovery request was rejected. Review the reason in the support ticket",
        [],
    ),
}


def _template_rows() -> list[dict]:
    rows = []
    for code, (title, body, variables) in TEMPLATES.items():
        for channel in ("in_app", "email"):
            rows.append(
                {
                    "id": uuid.uuid5(
                        uuid.NAMESPACE_URL, f"amp:notification:{code}:{channel}:1"
                    ),
                    "code": code,
                    "channel": channel,
                    "version": 1,
                    "title_template": title if channel == "in_app" else None,
                    "subject_template": title if channel == "email" else None,
                    "body_template": body,
                    "allowed_variables": variables,
                    "is_active": True,
                    "created_by_user_id": None,
                }
            )
    return rows


def _seed_templates() -> None:
    rows = _template_rows()
    table = sa.table(
        "notification_template_versions",
        sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()),
        sa.column("channel", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("title_template", sa.String()),
        sa.column("subject_template", sa.String()),
        sa.column("body_template", sa.Text()),
        sa.column("allowed_variables", sa.JSON()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_by_user_id", sa.Uuid()),
    )
    if op.get_bind().dialect.name != "postgresql":
        op.bulk_insert(table, rows)
        return
    statement = sa.text(
        "INSERT INTO notification_template_versions "
        "(id, code, channel, version, title_template, subject_template, body_template, "
        "allowed_variables, is_active, created_by_user_id) VALUES "
        "(CAST(:id AS uuid), :code, :channel, :version, :title_template, "
        ":subject_template, :body_template, CAST(:allowed_variables AS jsonb), "
        ":is_active, CAST(:created_by_user_id AS uuid))"
    )
    for row in rows:
        op.execute(
            statement.bindparams(
                id=str(row["id"]),
                code=row["code"],
                channel=row["channel"],
                version=row["version"],
                title_template=row["title_template"],
                subject_template=row["subject_template"],
                body_template=row["body_template"],
                allowed_variables=json.dumps(row["allowed_variables"]),
                is_active=True,
                created_by_user_id=None,
            )
        )


def _configure_postgresql_roles() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        DO $validate_lifecycle_role$
        DECLARE role_data record;
        BEGIN
            SELECT * INTO role_data FROM pg_roles WHERE rolname = 'amp_lifecycle_worker';
            IF role_data IS NULL OR NOT role_data.rolcanlogin OR role_data.rolsuper
                OR role_data.rolcreatedb OR role_data.rolcreaterole
                OR role_data.rolreplication OR role_data.rolbypassrls
                OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member = role_data.oid)
            THEN
                RAISE EXCEPTION 'Missing or unsafe amp_lifecycle_worker role';
            END IF;
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO amp_lifecycle_worker', current_database()
            );
        END
        $validate_lifecycle_role$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO amp_lifecycle_worker")
    op.execute(
        "GRANT SELECT (id, email, role, status, status_before_block, status_reason, "
        "status_changed_at, updated_at), UPDATE (status, status_before_block, status_reason, "
        "status_changed_at, updated_at) ON TABLE users TO amp_lifecycle_worker"
    )
    op.execute(
        "GRANT SELECT (user_id, revoked_at), UPDATE (revoked_at) "
        "ON TABLE refresh_sessions TO amp_lifecycle_worker"
    )
    op.execute(
        "GRANT SELECT, UPDATE ON TABLE creator_lifecycles, lifecycle_jobs "
        "TO amp_lifecycle_worker"
    )
    op.execute(
        "GRANT SELECT (id, user_id, status, moderation_reason), "
        "UPDATE (status, moderation_reason, updated_at) ON TABLE creator_profiles "
        "TO amp_lifecycle_worker"
    )
    op.execute(
        "GRANT SELECT (id, video_card_id, status, deleted_at), "
        "UPDATE (status, moderation_reason, enrichment_status, updated_at) "
        "ON TABLE publications TO amp_lifecycle_worker"
    )
    for table in ("youtube_enrichment_jobs", "youtube_view_collection_jobs"):
        op.execute(
            f"GRANT SELECT (publication_id, state), UPDATE (state, lease_until, "
            f"dispatch_id, last_error_code, updated_at) ON TABLE {table} "
            "TO amp_lifecycle_worker"
        )
    op.execute(
        "GRANT INSERT ON TABLE profile_history, publication_history TO amp_lifecycle_worker"
    )
    op.execute(
        "GRANT SELECT ON TABLE notification_template_versions TO amp_lifecycle_worker"
    )
    op.execute(
        "GRANT SELECT (id, blogger_id) ON TABLE video_cards TO amp_lifecycle_worker"
    )
    op.execute(
        "GRANT SELECT (blogger_id, available_kopecks, claim_expired_at) "
        "ON TABLE creator_balances TO amp_lifecycle_worker"
    )
    op.execute(
        "GRANT UPDATE (claim_expired_at, updated_at) "
        "ON TABLE creator_balances TO amp_lifecycle_worker"
    )
    op.execute(
        "GRANT SELECT, INSERT ON TABLE notifications TO amp_lifecycle_worker"
    )
    op.execute("GRANT INSERT ON TABLE outbox_events, security_events TO amp_lifecycle_worker")
    op.execute(
        "GRANT SELECT (id, role, status) ON TABLE users TO amp_scheduler"
    )
    op.execute(
        "GRANT SELECT ON TABLE creator_lifecycles TO amp_scheduler"
    )
    op.execute(
        "GRANT UPDATE (scheduler_scanned_at, updated_at) "
        "ON TABLE creator_lifecycles TO amp_scheduler"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE lifecycle_jobs TO amp_scheduler"
    )


def upgrade() -> None:
    op.add_column(
        "creator_balances",
        sa.Column("claim_expired_at", sa.DateTime(timezone=True), nullable=True),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION guard_balance_claim_expiration() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.claim_expired_at IS NOT NULL
                   AND NEW.claim_expired_at IS DISTINCT FROM OLD.claim_expired_at THEN
                    RAISE EXCEPTION 'balance claim expiration is immutable';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            "CREATE TRIGGER trg_creator_balance_claim_expiration "
            "BEFORE UPDATE ON creator_balances FOR EACH ROW "
            "EXECUTE FUNCTION guard_balance_claim_expiration()"
        )
    op.create_table(
        "creator_lifecycles",
        sa.Column("blogger_id", sa.Uuid(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_activity_kind", sa.String(32), nullable=False),
        sa.Column("activity_revision", sa.Integer(), nullable=False),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("balance_claim_expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduler_scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "last_activity_kind IN ('account_created', 'login', 'publication_changed', "
            "'reading_submitted', 'payout_requested', 'account_restored')",
            name="ck_creator_lifecycle_activity_kind",
        ),
        sa.CheckConstraint(
            "activity_revision > 0", name="ck_creator_lifecycle_revision_positive"
        ),
        sa.CheckConstraint(
            "(suspended_at IS NULL) OR suspended_at >= last_activity_at",
            name="ck_creator_lifecycle_suspension_order",
        ),
        sa.CheckConstraint(
            "(blocked_at IS NULL) OR (suspended_at IS NOT NULL AND blocked_at >= suspended_at)",
            name="ck_creator_lifecycle_block_order",
        ),
        sa.ForeignKeyConstraint(["blogger_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("blogger_id"),
    )
    op.create_index(
        "ix_creator_lifecycle_activity_due",
        "creator_lifecycles",
        ["last_activity_at", "suspended_at"],
    )
    op.create_index(
        "ix_creator_lifecycle_block_due",
        "creator_lifecycles",
        ["suspended_at", "blocked_at"],
    )
    op.create_index(
        "ix_creator_lifecycle_scan_due",
        "creator_lifecycles",
        ["scheduler_scanned_at", "blogger_id"],
    )
    op.create_table(
        "lifecycle_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("blogger_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("basis_revision", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("dispatch_id", sa.Uuid(), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("basis_revision > 0", name="ck_lifecycle_job_revision_positive"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_lifecycle_job_attempt_nonnegative"),
        sa.CheckConstraint(
            "action IN ('warn_suspension_30', 'warn_suspension_7', 'suspend', "
            "'warn_block_30', 'warn_block_7', 'block')",
            name="ck_lifecycle_job_action",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', "
            "'obsolete', 'failed')",
            name="ck_lifecycle_job_state",
        ),
        sa.ForeignKeyConstraint(["blogger_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dispatch_id", name="uq_lifecycle_jobs_dispatch_id"),
        sa.UniqueConstraint("blogger_id", "action", "basis_revision", name="uq_lifecycle_job_basis"),
    )
    op.create_index(
        "ix_lifecycle_jobs_due", "lifecycle_jobs", ["state", "available_at", "created_at"]
    )
    op.execute(
        "INSERT INTO creator_lifecycles "
        "(blogger_id, last_activity_at, last_activity_kind, activity_revision) "
        "SELECT id, created_at, 'account_created', 1 FROM users WHERE role = 'blogger'"
    )

    op.add_column(
        "support_ticket_events",
        sa.Column("recovery_decision", sa.String(16), nullable=True),
    )
    op.drop_constraint("ck_support_event_shape", "support_ticket_events", type_="check")
    op.drop_constraint("ck_support_event_type", "support_ticket_events", type_="check")
    op.create_check_constraint(
        "ck_support_event_type",
        "support_ticket_events",
        "event_type IN ('created', 'status_changed', 'assigned', 'recovery_decided')",
    )
    op.create_check_constraint(
        "ck_support_event_shape",
        "support_ticket_events",
        "(event_type = 'created' AND from_status IS NULL AND to_status = 'new' "
        "AND previous_assignee_user_id IS NULL AND new_assignee_user_id IS NULL "
        "AND recovery_decision IS NULL) OR "
        "(event_type = 'status_changed' AND from_status IS NOT NULL AND to_status IS NOT NULL "
        "AND previous_assignee_user_id IS NULL AND new_assignee_user_id IS NULL "
        "AND recovery_decision IS NULL) OR "
        "(event_type = 'assigned' AND from_status IS NULL AND to_status IS NULL "
        "AND recovery_decision IS NULL) OR "
        "(event_type = 'recovery_decided' AND from_status IS NOT NULL "
        "AND to_status = 'resolved' AND recovery_decision IN ('approve', 'reject') "
        "AND previous_assignee_user_id IS NULL AND new_assignee_user_id IS NULL)",
    )
    _seed_templates()
    _configure_postgresql_roles()


def downgrade() -> None:
    raise RuntimeError(
        "0022_account_lifecycle is irreversible because it contains account lifecycle history"
    )
