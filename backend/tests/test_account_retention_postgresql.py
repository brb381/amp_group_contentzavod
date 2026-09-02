import os
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit.service import AuditContext
from app.auth.models import AccountStatus, Role, User
from app.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationSeverity,
    NotificationTemplateVersion,
)
from app.retention.service import ANONYMIZED_TEXT, anonymize_eligible_account_pii
from app.support.models import (
    SupportCategory,
    SupportEventType,
    SupportMessage,
    SupportStatus,
    SupportTicket,
    SupportTicketEvent,
)


@pytest.fixture()
def postgres_engine():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL")
    engine = create_engine(database_url, pool_pre_ping=True, hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()


def test_postgresql_retention_is_the_only_allowed_history_rewrite(postgres_engine):
    blogger_id = uuid.uuid4()
    ticket_id = uuid.uuid4()
    message_id = uuid.uuid4()
    event_id = uuid.uuid4()
    notification_id = uuid.uuid4()
    old = datetime(2019, 1, 1, tzinfo=timezone.utc)
    anonymized_at = datetime(2026, 8, 30, tzinfo=timezone.utc)

    with Session(postgres_engine) as db, db.begin():
        user = User(
            id=blogger_id,
            email=f"account-retention-{blogger_id}@example.com",
            password_hash="not-used",
            role=Role.BLOGGER,
            status=AccountStatus.DELETED,
            status_changed_at=old,
            collaboration_ended_at=old,
            deleted_at=old,
        )
        db.add(user)
        template = NotificationTemplateVersion(
            code=f"retention-{blogger_id}",
            channel=NotificationChannel.IN_APP,
            version=1,
            title_template="Private title",
            body_template="Private body",
            allowed_variables=[],
        )
        db.add(template)
        db.flush()
        ticket = SupportTicket(
            id=ticket_id,
            ticket_number=f"RET-{blogger_id.hex[:16]}",
            blogger_id=blogger_id,
            category=SupportCategory.GENERAL,
            subject="Private subject",
            status=SupportStatus.CLOSED,
            creation_idempotency_key=uuid.uuid4(),
            creation_payload_hash="a" * 64,
            created_at=old,
            updated_at=old,
            last_message_at=old,
            resolved_at=old,
            closed_at=old,
        )
        db.add(ticket)
        db.flush()
        db.add_all(
            [
                SupportMessage(
                    id=message_id,
                    ticket_id=ticket_id,
                    author_user_id=blogger_id,
                    author_role=Role.BLOGGER.value,
                    body="Private support message",
                    idempotency_key=uuid.uuid4(),
                    payload_hash="b" * 64,
                    created_at=old,
                ),
                SupportTicketEvent(
                    id=event_id,
                    ticket_id=ticket_id,
                    event_type=SupportEventType.CREATED,
                    actor_user_id=blogger_id,
                    to_status=SupportStatus.NEW,
                    reason="Private reason",
                    idempotency_key=uuid.uuid4(),
                    payload_hash="c" * 64,
                    created_at=old,
                ),
                Notification(
                    id=notification_id,
                    recipient_user_id=blogger_id,
                    template_code=template.code,
                    template_version_id=template.id,
                    severity=NotificationSeverity.INFO,
                    title="Private title",
                    body="Private notification",
                    action_path="/private/path",
                    deduplication_key=f"retention:{blogger_id}",
                    created_at=old,
                ),
            ]
        )

    with Session(postgres_engine) as db:
        with pytest.raises(DBAPIError):
            db.execute(
                update(SupportMessage)
                .where(SupportMessage.id == message_id)
                .values(body="Rewritten before retention")
            )
            db.commit()
        db.rollback()

    with Session(postgres_engine) as db, db.begin():
        result = anonymize_eligible_account_pii(
            db,
            cutoff=date(2021, 1, 1),
            anonymized_at=anonymized_at,
            audit_context=AuditContext(
                request_id=f"account-retention-pg-{blogger_id}",
                ip_address="127.0.0.1",
                user_agent="pytest",
            ),
        )
        assert result.accounts == 1
        assert result.support_records == 3
        assert result.notifications == 1

    with Session(postgres_engine) as db:
        message = db.get(SupportMessage, message_id)
        event = db.get(SupportTicketEvent, event_id)
        notification = db.get(Notification, notification_id)
        assert message.body == ANONYMIZED_TEXT
        assert event.reason is None
        assert notification.title == notification.body == ANONYMIZED_TEXT
        assert notification.action_path is None
        assert db.scalar(select(User.pii_anonymized_at).where(User.id == blogger_id))

        with pytest.raises(DBAPIError):
            db.execute(
                update(Notification)
                .where(Notification.id == notification_id)
                .values(body="Restored private notification")
            )
            db.commit()
        db.rollback()
