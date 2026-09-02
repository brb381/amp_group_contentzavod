"""Add self-service account deletion and coordinated PII retention.

Revision ID: 0023_account_deletion_retention
Revises: 0022_account_lifecycle
Create Date: 2026-08-30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0023_account_deletion_retention"
down_revision: Union[str, Sequence[str], None] = "0022_account_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_retention_columns() -> None:
    for column in ("collaboration_ended_at", "deleted_at", "pii_anonymized_at"):
        op.add_column("users", sa.Column(column, sa.DateTime(timezone=True), nullable=True))
    for table in (
        "creator_profiles",
        "social_accounts",
        "profile_history",
        "social_account_history",
        "publication_history",
        "support_tickets",
        "support_messages",
        "support_ticket_events",
        "notifications",
        "outbox_events",
    ):
        op.add_column(
            table,
            sa.Column("pii_anonymized_at", sa.DateTime(timezone=True), nullable=True),
        )
    op.create_index(
        "ix_users_account_retention",
        "users",
        ["status", "collaboration_ended_at", "pii_anonymized_at"],
    )


def _create_deletion_tables() -> None:
    op.create_table(
        "account_deletion_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("blogger_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("creation_idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("creation_payload_hash", sa.String(64), nullable=False),
        sa.Column("confirmation_token_hash", sa.String(64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('awaiting_confirmation', 'completed', 'cancelled', 'expired')",
            name="ck_account_deletion_request_status",
        ),
        sa.CheckConstraint(
            "expires_at > requested_at", name="ck_account_deletion_request_expiry"
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND cancelled_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('awaiting_confirmation', 'expired') AND completed_at IS NULL "
            "AND cancelled_at IS NULL)",
            name="ck_account_deletion_request_terminal_shape",
        ),
        sa.ForeignKeyConstraint(["blogger_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "blogger_id",
            "creation_idempotency_key",
            name="uq_account_deletion_creation_key",
        ),
        sa.UniqueConstraint(
            "confirmation_token_hash", name="uq_account_deletion_token_hash"
        ),
    )
    op.create_index(
        "uq_account_deletion_active_blogger",
        "account_deletion_requests",
        ["blogger_id"],
        unique=True,
        postgresql_where=sa.text("status = 'awaiting_confirmation'"),
        sqlite_where=sa.text("status = 'awaiting_confirmation'"),
    )
    op.create_index(
        "ix_account_deletion_blogger_requested",
        "account_deletion_requests",
        ["blogger_id", "requested_at"],
    )
    op.create_table(
        "account_deletion_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "action IN ('requested', 'completed', 'cancelled', 'expired')",
            name="ck_account_deletion_event_action",
        ),
        sa.CheckConstraint(
            "length(payload_hash) = 64", name="ck_account_deletion_event_payload_hash"
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["account_deletion_requests.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_user_id",
            "idempotency_key",
            name="uq_account_deletion_event_actor_key",
        ),
    )
    op.create_index(
        "ix_account_deletion_event_request_created",
        "account_deletion_events",
        ["request_id", "created_at"],
    )


def _configure_roles() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("REVOKE DELETE ON TABLE account_deletion_requests FROM amp_api")
    op.execute("REVOKE UPDATE, DELETE ON TABLE account_deletion_events FROM amp_api")
    op.execute("REVOKE SELECT ON TABLE users FROM amp_retention_worker")
    op.execute(
        "GRANT SELECT (id, status, collaboration_ended_at, pii_anonymized_at, email), "
        "UPDATE (email, password_hash, status_reason, email_verified_at, pii_anonymized_at, "
        "updated_at) ON TABLE users TO amp_retention_worker"
    )
    op.execute(
        "GRANT SELECT (id, user_id, full_name, display_name, phone, telegram, city_country, "
        "content_topics, moderation_reason, pii_anonymized_at), UPDATE (full_name, "
        "display_name, phone, telegram, city_country, content_topics, moderation_reason, "
        "pii_anonymized_at, updated_at) ON TABLE creator_profiles TO amp_retention_worker"
    )
    op.execute(
        "GRANT SELECT (id, user_id, url, moderation_reason, pii_anonymized_at), "
        "UPDATE (url, moderation_reason, pii_anonymized_at, updated_at) "
        "ON TABLE social_accounts TO amp_retention_worker"
    )
    for table in (
        "profile_history",
        "social_account_history",
        "publication_history",
        "support_tickets",
        "support_messages",
        "support_ticket_events",
        "notifications",
        "outbox_events",
    ):
        op.execute(f"GRANT SELECT ON TABLE {table} TO amp_retention_worker")
    op.execute("GRANT UPDATE (reason, changes, pii_anonymized_at) ON profile_history TO amp_retention_worker")
    op.execute("GRANT UPDATE (reason, pii_anonymized_at) ON social_account_history TO amp_retention_worker")
    op.execute("GRANT UPDATE (reason, changes, pii_anonymized_at) ON publication_history TO amp_retention_worker")
    op.execute("GRANT UPDATE (subject, pii_anonymized_at, updated_at) ON support_tickets TO amp_retention_worker")
    op.execute("GRANT UPDATE (body, pii_anonymized_at) ON support_messages TO amp_retention_worker")
    op.execute("GRANT UPDATE (reason, pii_anonymized_at) ON support_ticket_events TO amp_retention_worker")
    op.execute("GRANT UPDATE (title, body, action_path, pii_anonymized_at) ON notifications TO amp_retention_worker")
    op.execute("GRANT UPDATE (payload, pii_anonymized_at) ON outbox_events TO amp_retention_worker")
    op.execute("GRANT SELECT ON TABLE video_cards, publications TO amp_retention_worker")
    op.execute(
        "GRANT SELECT (id, status) ON TABLE users TO amp_calculation_worker"
    )


def _replace_append_only_guards() -> None:
    op.execute(
        """
        DROP TRIGGER trg_support_messages_append_only ON support_messages;
        CREATE FUNCTION guard_support_message_retention() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.pii_anonymized_at IS NOT NULL OR NOT (
                NEW.pii_anonymized_at IS NOT NULL
                AND NEW.body = '[anonymized]'
                AND NEW.id = OLD.id
                AND NEW.ticket_id = OLD.ticket_id
                AND NEW.author_user_id = OLD.author_user_id
                AND NEW.author_role = OLD.author_role
                AND NEW.idempotency_key = OLD.idempotency_key
                AND NEW.payload_hash = OLD.payload_hash
                AND NEW.created_at = OLD.created_at
            ) THEN
                RAISE EXCEPTION 'support messages are append-only except controlled anonymization';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_support_messages_append_only
            BEFORE UPDATE OR DELETE ON support_messages
            FOR EACH ROW EXECUTE FUNCTION guard_support_message_retention();

        DROP TRIGGER trg_support_events_append_only ON support_ticket_events;
        CREATE FUNCTION guard_support_event_retention() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.pii_anonymized_at IS NOT NULL OR NOT (
                NEW.pii_anonymized_at IS NOT NULL
                AND NEW.reason IS NULL
                AND NEW.id = OLD.id
                AND NEW.ticket_id = OLD.ticket_id
                AND NEW.event_type = OLD.event_type
                AND NEW.actor_user_id = OLD.actor_user_id
                AND NEW.from_status IS NOT DISTINCT FROM OLD.from_status
                AND NEW.to_status IS NOT DISTINCT FROM OLD.to_status
                AND NEW.previous_assignee_user_id IS NOT DISTINCT FROM OLD.previous_assignee_user_id
                AND NEW.new_assignee_user_id IS NOT DISTINCT FROM OLD.new_assignee_user_id
                AND NEW.recovery_decision IS NOT DISTINCT FROM OLD.recovery_decision
                AND NEW.idempotency_key = OLD.idempotency_key
                AND NEW.payload_hash = OLD.payload_hash
                AND NEW.created_at = OLD.created_at
            ) THEN
                RAISE EXCEPTION 'support events are append-only except controlled anonymization';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_support_events_append_only
            BEFORE UPDATE OR DELETE ON support_ticket_events
            FOR EACH ROW EXECUTE FUNCTION guard_support_event_retention();

        DROP TRIGGER trg_notifications_mutation_guard ON notifications;
        CREATE FUNCTION guard_notification_retention() RETURNS trigger AS $$
        DECLARE
            marking_read boolean;
            anonymizing boolean;
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.pii_anonymized_at IS NOT NULL THEN
                RAISE EXCEPTION 'notification history cannot be deleted or restored';
            END IF;
            marking_read := NEW.pii_anonymized_at IS NULL
                AND NEW.recipient_user_id = OLD.recipient_user_id
                AND NEW.template_code = OLD.template_code
                AND NEW.template_version_id = OLD.template_version_id
                AND NEW.severity = OLD.severity
                AND NEW.title = OLD.title
                AND NEW.body = OLD.body
                AND NEW.related_object_type IS NOT DISTINCT FROM OLD.related_object_type
                AND NEW.related_object_id IS NOT DISTINCT FROM OLD.related_object_id
                AND NEW.action_path IS NOT DISTINCT FROM OLD.action_path
                AND NEW.deduplication_key = OLD.deduplication_key
                AND NEW.created_at = OLD.created_at
                AND NEW.expires_at IS NOT DISTINCT FROM OLD.expires_at
                AND OLD.read_at IS NULL AND NEW.read_at IS NOT NULL;
            anonymizing := NEW.pii_anonymized_at IS NOT NULL
                AND NEW.title = '[anonymized]'
                AND NEW.body = '[anonymized]'
                AND NEW.action_path IS NULL
                AND NEW.recipient_user_id = OLD.recipient_user_id
                AND NEW.template_code = OLD.template_code
                AND NEW.template_version_id = OLD.template_version_id
                AND NEW.severity = OLD.severity
                AND NEW.related_object_type IS NOT DISTINCT FROM OLD.related_object_type
                AND NEW.related_object_id IS NOT DISTINCT FROM OLD.related_object_id
                AND NEW.deduplication_key = OLD.deduplication_key
                AND NEW.read_at IS NOT DISTINCT FROM OLD.read_at
                AND NEW.created_at = OLD.created_at
                AND NEW.expires_at IS NOT DISTINCT FROM OLD.expires_at;
            IF NOT marking_read AND NOT anonymizing THEN
                RAISE EXCEPTION 'notification content is immutable except controlled anonymization';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_notifications_mutation_guard
            BEFORE UPDATE OR DELETE ON notifications
            FOR EACH ROW EXECUTE FUNCTION guard_notification_retention();
        """
    )


def _protect_deletion_history() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_account_deletion_event_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'account deletion history is append-only';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_account_deletion_events_append_only
            BEFORE UPDATE OR DELETE ON account_deletion_events
            FOR EACH ROW EXECUTE FUNCTION reject_account_deletion_event_mutation();
        """
    )


def upgrade() -> None:
    _add_retention_columns()
    _create_deletion_tables()
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION guard_account_deletion_request() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' OR OLD.status <> 'awaiting_confirmation'
                   OR NEW.status NOT IN ('completed', 'cancelled', 'expired')
                   OR OLD.blogger_id <> NEW.blogger_id
                   OR OLD.creation_idempotency_key <> NEW.creation_idempotency_key
                   OR OLD.creation_payload_hash <> NEW.creation_payload_hash
                   OR OLD.confirmation_token_hash <> NEW.confirmation_token_hash
                   OR OLD.requested_at <> NEW.requested_at
                   OR OLD.expires_at <> NEW.expires_at
                   OR OLD.created_at <> NEW.created_at THEN
                    RAISE EXCEPTION 'invalid account deletion request mutation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER trg_account_deletion_request_guard
                BEFORE UPDATE OR DELETE ON account_deletion_requests
                FOR EACH ROW EXECUTE FUNCTION guard_account_deletion_request();
            """
        )
        _replace_append_only_guards()
        _protect_deletion_history()
    _configure_roles()


def downgrade() -> None:
    raise RuntimeError(
        "0023_account_deletion_retention is irreversible because account deletion and "
        "PII anonymization cannot be safely undone"
    )
