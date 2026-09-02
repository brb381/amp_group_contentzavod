import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
import pytest

from app.audit.models import SecurityEvent
from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password
from app.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationTemplateVersion,
)
from app.outbox.models import OutboxEvent
from app.support.models import SupportMessage, SupportTicket, SupportTicketEvent


PASSWORD = "support-test-password-123"


def _csrf(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("amp_csrf")}


def _seed_user(client, role: Role, status: AccountStatus = AccountStatus.ACTIVE):
    email = f"support-{role.value}-{uuid.uuid4()}@example.com"
    with client.app.state.test_session() as db:
        user = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=role,
            status=status,
            email_verified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        db.add(user)
        db.commit()
        return user.id, email


def _login(client, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text


def _seed_templates(client) -> None:
    with client.app.state.test_session() as db:
        if db.scalar(select(func.count()).select_from(NotificationTemplateVersion)):
            return
    definitions = {
        "support_ticket_created": ["ticket_number", "subject"],
        "support_blogger_message": ["ticket_number", "subject"],
        "support_staff_message": ["status", "subject", "ticket_number"],
        "support_ticket_assigned": ["ticket_number", "subject"],
        "support_status_changed": ["status", "subject", "ticket_number"],
    }
    with client.app.state.test_session() as db:
        for code, variables in definitions.items():
            db.add_all(
                [
                    NotificationTemplateVersion(
                        code=code,
                        channel=NotificationChannel.IN_APP,
                        version=1,
                        title_template="$ticket_number",
                        body_template="$subject" + (" - $status" if "status" in variables else ""),
                        allowed_variables=variables,
                    ),
                    NotificationTemplateVersion(
                        code=code,
                        channel=NotificationChannel.EMAIL,
                        version=1,
                        subject_template="$ticket_number",
                        body_template="$subject" + (" - $status" if "status" in variables else ""),
                        allowed_variables=variables,
                    ),
                ]
            )
        db.commit()


def _create_ticket(client, *, category: str = "general", key: uuid.UUID | None = None):
    return client.post(
        "/api/v1/me/support-tickets",
        headers=_csrf(client),
        json={
            "idempotency_key": str(key or uuid.uuid4()),
            "category": category,
            "subject": "Cannot publish a video",
            "body": "The submit action returns an error.",
        },
    )


def test_support_workflow_is_atomic_idempotent_and_notifies(client):
    _seed_templates(client)
    blogger_id, blogger_email = _seed_user(client, Role.BLOGGER)
    manager_id, manager_email = _seed_user(client, Role.MANAGER)

    _login(client, blogger_email)
    key = uuid.uuid4()
    created = _create_ticket(client, key=key)
    assert created.status_code == 201, created.text
    ticket_id = created.json()["id"]
    replay = _create_ticket(client, key=key)
    assert replay.status_code == 201
    assert replay.json()["id"] == ticket_id

    with client.app.state.test_session() as db:
        assert db.scalar(select(func.count()).select_from(SupportTicket)) == 1
        assert db.scalar(select(func.count()).select_from(SupportMessage)) == 1
        assert db.scalar(select(func.count()).select_from(SupportTicketEvent)) == 1
        manager_notifications = list(
            db.scalars(select(Notification).where(Notification.recipient_user_id == manager_id))
        )
        assert len(manager_notifications) == 1
        assert manager_notifications[0].body.endswith("Cannot publish a video")
        assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 1

    _login(client, manager_email)
    staff_reply = client.post(
        f"/api/v1/staff/support-tickets/{ticket_id}/messages",
        headers=_csrf(client),
        json={"idempotency_key": str(uuid.uuid4()), "body": "Please retry now."},
    )
    assert staff_reply.status_code == 201, staff_reply.text
    detail = client.get(f"/api/v1/staff/support-tickets/{ticket_id}")
    assert detail.status_code == 200
    assert detail.json()["status"] == "waiting_blogger"
    assert len(detail.json()["messages"]) == 2
    assert detail.json()["assigned_to_user_id"] == str(manager_id)

    _login(client, blogger_email)
    notifications = client.get("/api/v1/me/notifications?unreadOnly=true")
    assert notifications.status_code == 200
    assert notifications.headers["cache-control"] == "private, no-store"
    assert notifications.json()["total_items"] == 1
    assert "author_user_id" not in client.get(
        f"/api/v1/me/support-tickets/{ticket_id}"
    ).json()["messages"][1]
    notification_id = notifications.json()["items"][0]["id"]
    assert client.post(
        f"/api/v1/me/notifications/{notification_id}/reads", headers=_csrf(client)
    ).status_code == 200
    assert client.get("/api/v1/me/notifications/unread-count").json()["unread_count"] == 0

    with client.app.state.test_session() as db:
        audit = db.scalar(
            select(SecurityEvent).where(SecurityEvent.action == "support.message_created")
        )
        assert audit is not None
        assert "Please retry now" not in str(audit.event_metadata)


def test_suspended_blogger_can_only_create_recovery_ticket(client):
    _seed_templates(client)
    _, email = _seed_user(client, Role.BLOGGER, AccountStatus.SUSPENDED)
    _login(client, email)
    ordinary = _create_ticket(client)
    assert ordinary.status_code == 409
    assert ordinary.json()["error"]["code"] == "RECOVERY_TICKET_REQUIRED"
    recovery = _create_ticket(client, category="account_recovery")
    assert recovery.status_code == 201, recovery.text
    assert client.get("/api/v1/me/support-tickets").status_code == 200


def test_support_category_role_isolation_and_status_transitions(client):
    _seed_templates(client)
    _, blogger_email = _seed_user(client, Role.BLOGGER, AccountStatus.SUSPENDED)
    _, manager_email = _seed_user(client, Role.MANAGER)
    _, moderator_email = _seed_user(client, Role.MODERATOR)

    _login(client, blogger_email)
    created = _create_ticket(client, category="account_recovery")
    ticket_id = created.json()["id"]

    _login(client, manager_email)
    assert client.get(f"/api/v1/staff/support-tickets/{ticket_id}").status_code == 404
    assert client.get("/api/v1/staff/support-tickets").json()["total_items"] == 0

    _login(client, moderator_email)
    transition = client.post(
        f"/api/v1/staff/support-tickets/{ticket_id}/status-transitions",
        headers=_csrf(client),
        json={"idempotency_key": str(uuid.uuid4()), "to_status": "in_progress"},
    )
    assert transition.status_code == 201, transition.text
    assert transition.json()["to_status"] == "in_progress"
    invalid = client.post(
        f"/api/v1/staff/support-tickets/{ticket_id}/status-transitions",
        headers=_csrf(client),
        json={"idempotency_key": str(uuid.uuid4()), "to_status": "closed"},
    )
    assert invalid.status_code == 409


def test_admin_can_version_templates_without_rewriting_history(client):
    _seed_templates(client)
    _, admin_email = _seed_user(client, Role.ADMIN)
    _login(client, admin_email)
    response = client.post(
        "/api/v1/admin/notification-templates/support_ticket_created/email/versions",
        headers=_csrf(client),
        json={
            "subject_template": "Ticket $ticket_number",
            "body_template": "New request: $subject",
            "allowed_variables": ["ticket_number", "subject"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["version"] == 2
    with client.app.state.test_session() as db:
        versions = list(
            db.scalars(
                select(NotificationTemplateVersion)
                .where(
                    NotificationTemplateVersion.code == "support_ticket_created",
                    NotificationTemplateVersion.channel == NotificationChannel.EMAIL,
                )
                .order_by(NotificationTemplateVersion.version)
            )
        )
        assert [(item.version, item.is_active) for item in versions] == [(1, False), (2, True)]

    rejected = client.post(
        "/api/v1/admin/notification-templates/support_ticket_created/email/versions",
        headers=_csrf(client),
        json={
            "subject_template": "Bad\nBcc: $subject",
            "body_template": "$subject",
            "allowed_variables": ["subject"],
        },
    )
    assert rejected.status_code == 422


def test_ticket_creation_rolls_back_when_notification_port_fails(client, monkeypatch):
    _, blogger_email = _seed_user(client, Role.BLOGGER)
    _seed_user(client, Role.MANAGER)
    _login(client, blogger_email)

    def fail_notification(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("notification storage unavailable")

    monkeypatch.setattr("app.support.service.create_notification", fail_notification)
    with pytest.raises(RuntimeError, match="notification storage unavailable"):
        _create_ticket(client)
    with client.app.state.test_session() as db:
        assert db.scalar(select(func.count()).select_from(SupportTicket)) == 0
        assert db.scalar(select(func.count()).select_from(SupportMessage)) == 0
        assert db.scalar(select(func.count()).select_from(SupportTicketEvent)) == 0


def test_support_messages_and_events_are_append_only(client):
    _, blogger_email = _seed_user(client, Role.BLOGGER)
    _seed_user(client, Role.MANAGER)
    _login(client, blogger_email)
    created = _create_ticket(client)
    assert created.status_code == 201

    with client.app.state.test_session() as db:
        message = db.scalar(select(SupportMessage))
        message.body = "rewritten"
        with pytest.raises(ValueError, match="append-only"):
            db.flush()
        db.rollback()

        event = db.scalar(select(SupportTicketEvent))
        event.reason = "rewritten"
        with pytest.raises(ValueError, match="append-only"):
            db.flush()
