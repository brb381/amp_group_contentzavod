"""Add durable notifications and support tickets.

Revision ID: 0021_notifications_support
Revises: 0020_payout_pii_retention
Create Date: 2026-08-30
"""

import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0021_notifications_support"
down_revision: Union[str, Sequence[str], None] = "0020_payout_pii_retention"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _template_rows() -> list[dict]:
    definitions = {
        "registration_created": (
            "Registration created",
            "Confirm your email address to activate the account",
            [],
        ),
        "email_verified": (
            "Email confirmed",
            "Your email address has been confirmed",
            [],
        ),
        "profile_status_changed": (
            "Profile status changed",
            "Your profile status is now $status",
            ["status"],
        ),
        "publication_submitted": (
            "Publication submitted",
            "Publication $publication_id is ready for review",
            ["publication_id"],
        ),
        "publication_status_changed": (
            "Publication status changed",
            "Publication $publication_id is now $status",
            ["publication_id", "status"],
        ),
        "reading_status_changed": (
            "View reading reviewed",
            "Reading $reading_id is now $status",
            ["reading_id", "status"],
        ),
        "calculation_confirmed": (
            "Monthly calculation confirmed",
            "Calculation for $period was confirmed: $amount_kopecks kopecks",
            ["amount_kopecks", "period"],
        ),
        "payout_status_changed": (
            "Payout status changed",
            "Payout $request_number is now $status",
            ["request_number", "status"],
        ),
        "support_ticket_created": (
            "New support ticket $ticket_number",
            "A support ticket was created: $subject",
            ["ticket_number", "subject"],
        ),
        "support_blogger_message": (
            "New reply in $ticket_number",
            "The blogger replied to: $subject",
            ["ticket_number", "subject"],
        ),
        "support_staff_message": (
            "Support replied in $ticket_number",
            "There is a new support reply for: $subject. Current status: $status",
            ["status", "subject", "ticket_number"],
        ),
        "support_ticket_assigned": (
            "Ticket $ticket_number assigned",
            "You are responsible for: $subject",
            ["ticket_number", "subject"],
        ),
        "support_status_changed": (
            "Ticket $ticket_number status changed",
            "The status of $subject is now $status",
            ["status", "subject", "ticket_number"],
        ),
    }
    rows = []
    for code, (title, body, variables) in definitions.items():
        rows.extend(
            [
                {
                    "id": uuid.uuid5(uuid.NAMESPACE_URL, f"amp:notification:{code}:in_app:1"),
                    "code": code,
                    "channel": "in_app",
                    "version": 1,
                    "title_template": title,
                    "subject_template": None,
                    "body_template": body,
                    "allowed_variables": variables,
                    "is_active": True,
                    "created_by_user_id": None,
                },
                {
                    "id": uuid.uuid5(uuid.NAMESPACE_URL, f"amp:notification:{code}:email:1"),
                    "code": code,
                    "channel": "email",
                    "version": 1,
                    "title_template": None,
                    "subject_template": title,
                    "body_template": body,
                    "allowed_variables": variables,
                    "is_active": True,
                    "created_by_user_id": None,
                },
            ]
        )
    return rows


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("correlation_type", sa.String(64), nullable=True))
    op.add_column("outbox_events", sa.Column("correlation_id", sa.Uuid(), nullable=True))
    op.create_index("ix_outbox_events_correlation_type", "outbox_events", ["correlation_type"])
    op.create_index("ix_outbox_events_correlation_id", "outbox_events", ["correlation_id"])

    op.create_table(
        "notification_template_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title_template", sa.String(255), nullable=True),
        sa.Column("subject_template", sa.String(998), nullable=True),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column(
            "allowed_variables",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_notification_template_version_positive"),
        sa.CheckConstraint("length(trim(code)) > 0", name="ck_notification_template_code_required"),
        sa.CheckConstraint("length(trim(body_template)) > 0", name="ck_notification_template_body_required"),
        sa.CheckConstraint("channel IN ('in_app', 'email')", name="ck_notification_template_channel"),
        sa.CheckConstraint(
            "(channel = 'in_app' AND title_template IS NOT NULL AND subject_template IS NULL) OR "
            "(channel = 'email' AND subject_template IS NOT NULL AND title_template IS NULL)",
            name="ck_notification_template_channel_fields",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "channel", "version", name="uq_notification_template_version"),
    )
    op.create_index(
        "uq_notification_template_active",
        "notification_template_versions",
        ["code", "channel"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        sqlite_where=sa.text("is_active = 1"),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("template_code", sa.String(100), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("related_object_type", sa.String(64), nullable=True),
        sa.Column("related_object_id", sa.Uuid(), nullable=True),
        sa.Column("action_path", sa.String(512), nullable=True),
        sa.Column("deduplication_key", sa.String(255), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(template_code)) > 0", name="ck_notifications_code_required"),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_notifications_title_required"),
        sa.CheckConstraint("length(trim(body)) > 0", name="ck_notifications_body_required"),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'action_required')",
            name="ck_notifications_severity",
        ),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["template_version_id"], ["notification_template_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recipient_user_id", "deduplication_key", name="uq_notifications_recipient_dedupe"
        ),
    )
    op.create_index("ix_notifications_recipient_created", "notifications", ["recipient_user_id", "created_at"])
    op.create_index(
        "ix_notifications_recipient_unread",
        "notifications",
        ["recipient_user_id", "read_at", "created_at"],
    )

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_number", sa.String(40), nullable=False),
        sa.Column("blogger_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(24), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("assigned_to_user_id", sa.Uuid(), nullable=True),
        sa.Column("related_object_type", sa.String(64), nullable=True),
        sa.Column("related_object_id", sa.Uuid(), nullable=True),
        sa.Column("creation_idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("creation_payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(ticket_number)) > 0", name="ck_support_ticket_number_required"),
        sa.CheckConstraint("length(trim(subject)) > 0", name="ck_support_ticket_subject_required"),
        sa.CheckConstraint("length(creation_payload_hash) = 64", name="ck_support_ticket_payload_hash"),
        sa.CheckConstraint(
            "category IN ('general', 'content', 'payment', 'technical', 'account_recovery')",
            name="ck_support_ticket_category",
        ),
        sa.CheckConstraint(
            "status IN ('new', 'in_progress', 'waiting_blogger', 'resolved', 'closed')",
            name="ck_support_ticket_status",
        ),
        sa.CheckConstraint("(resolved_at IS NULL) OR status IN ('resolved', 'closed')", name="ck_support_ticket_resolved_scope"),
        sa.CheckConstraint("(closed_at IS NULL) OR status = 'closed'", name="ck_support_ticket_closed_scope"),
        sa.CheckConstraint(
            "(related_object_type IS NULL) = (related_object_id IS NULL)",
            name="ck_support_ticket_related_object_pair",
        ),
        sa.ForeignKeyConstraint(["blogger_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_to_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_number", name="uq_support_ticket_number"),
        sa.UniqueConstraint("blogger_id", "creation_idempotency_key", name="uq_support_ticket_creation_key"),
    )
    op.create_index("ix_support_ticket_blogger_updated", "support_tickets", ["blogger_id", "updated_at"])
    op.create_index("ix_support_ticket_queue", "support_tickets", ["status", "category", "updated_at"])
    op.create_index("ix_support_ticket_assignee", "support_tickets", ["assigned_to_user_id", "status"])

    op.create_table(
        "support_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column("author_role", sa.String(32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(trim(body)) > 0", name="ck_support_message_body_required"),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_support_message_payload_hash"),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("author_user_id", "idempotency_key", name="uq_support_message_author_key"),
    )
    op.create_index("ix_support_message_ticket_created", "support_messages", ["ticket_id", "created_at"])

    op.create_table(
        "support_ticket_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=True),
        sa.Column("to_status", sa.String(24), nullable=True),
        sa.Column("previous_assignee_user_id", sa.Uuid(), nullable=True),
        sa.Column("new_assignee_user_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("event_type IN ('created', 'status_changed', 'assigned')", name="ck_support_event_type"),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_support_event_payload_hash"),
        sa.CheckConstraint(
            "(event_type = 'created' AND from_status IS NULL AND to_status = 'new' "
            "AND previous_assignee_user_id IS NULL AND new_assignee_user_id IS NULL) OR "
            "(event_type = 'status_changed' AND from_status IS NOT NULL AND to_status IS NOT NULL "
            "AND previous_assignee_user_id IS NULL AND new_assignee_user_id IS NULL) OR "
            "(event_type = 'assigned' AND from_status IS NULL AND to_status IS NULL)",
            name="ck_support_event_shape",
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["support_tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_user_id", "idempotency_key", name="uq_support_event_actor_key"
        ),
    )
    op.create_index("ix_support_event_ticket_created", "support_ticket_events", ["ticket_id", "created_at"])

    template_table = sa.table(
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
    template_rows = _template_rows()
    if op.get_bind().dialect.name == "postgresql":
        insert_template = sa.text(
            "INSERT INTO notification_template_versions "
            "(id, code, channel, version, title_template, subject_template, body_template, "
            "allowed_variables, is_active, created_by_user_id) VALUES "
            "(CAST(:id AS uuid), :code, :channel, :version, :title_template, "
            ":subject_template, :body_template, CAST(:allowed_variables AS jsonb), "
            ":is_active, CAST(:created_by_user_id AS uuid))"
        )
        for row in template_rows:
            op.execute(
                insert_template.bindparams(
                    id=str(row["id"]),
                    code=row["code"],
                    channel=row["channel"],
                    version=row["version"],
                    title_template=row["title_template"],
                    subject_template=row["subject_template"],
                    body_template=row["body_template"],
                    allowed_variables=json.dumps(row["allowed_variables"]),
                    is_active=row["is_active"],
                    created_by_user_id=None,
                )
            )
    else:
        op.bulk_insert(template_table, template_rows)

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_support_history_mutation() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'support history is append-only';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER trg_support_messages_append_only
                BEFORE UPDATE OR DELETE ON support_messages
                FOR EACH ROW EXECUTE FUNCTION reject_support_history_mutation();
            CREATE TRIGGER trg_support_events_append_only
                BEFORE UPDATE OR DELETE ON support_ticket_events
                FOR EACH ROW EXECUTE FUNCTION reject_support_history_mutation();
            """
        )
        op.execute(
            """
            CREATE FUNCTION guard_notification_template_version() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' OR NOT (
                    OLD.is_active = true AND NEW.is_active = false
                    AND NEW.code = OLD.code AND NEW.channel = OLD.channel
                    AND NEW.version = OLD.version
                    AND NEW.title_template IS NOT DISTINCT FROM OLD.title_template
                    AND NEW.subject_template IS NOT DISTINCT FROM OLD.subject_template
                    AND NEW.body_template = OLD.body_template
                    AND NEW.allowed_variables = OLD.allowed_variables
                    AND NEW.created_by_user_id IS NOT DISTINCT FROM OLD.created_by_user_id
                    AND NEW.created_at = OLD.created_at
                ) THEN
                    RAISE EXCEPTION 'notification template versions are immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER trg_notification_template_version_guard
                BEFORE UPDATE OR DELETE ON notification_template_versions
                FOR EACH ROW EXECUTE FUNCTION guard_notification_template_version();
            """
        )
        op.execute(
            """
            CREATE FUNCTION guard_notification_mutation() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' OR NOT (
                    NEW.recipient_user_id = OLD.recipient_user_id
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
                    AND OLD.read_at IS NULL AND NEW.read_at IS NOT NULL
                ) THEN
                    RAISE EXCEPTION 'notification content is immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER trg_notifications_mutation_guard
                BEFORE UPDATE OR DELETE ON notifications
                FOR EACH ROW EXECUTE FUNCTION guard_notification_mutation();
            """
        )


def downgrade() -> None:
    raise RuntimeError(
        "0021_notifications_support is irreversible because it contains support and notification history"
    )
