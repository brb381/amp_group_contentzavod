import json

import pytest
from sqlalchemy import func, select

from app.audit.models import SecurityEvent
from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password, utc_now


PASSWORD = "correct-horse-battery-staple"
CONTEXT = AuditContext(
    request_id="transaction-test",
    ip_address="127.0.0.1",
    user_agent="pytest",
)


def register(client, email: str) -> None:
    documents = client.get("/api/v1/legal-documents/current").json()
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "program_terms_document_id": documents["program_terms"]["id"],
            "program_terms_accepted": True,
            "personal_data_consent_document_id": documents["personal_data_consent"]["id"],
            "personal_data_consent_granted": True,
        },
    )
    assert response.status_code == 201


def create_admin(client, email: str = "audit-admin@example.com") -> None:
    with client.app.state.test_session() as db:
        db.add(
            User(
                email=email,
                password_hash=hash_password(PASSWORD),
                role=Role.ADMIN,
                status=AccountStatus.ACTIVE,
                email_verified_at=utc_now(),
            )
        )
        db.commit()


def test_failed_login_is_persisted_without_credentials(client):
    email = "audit-login@example.com"
    wrong_password = "wrong-password-value"
    register(client, email)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": wrong_password},
        headers={"X-Request-ID": "failed-login-request"},
    )

    assert response.status_code == 401
    with client.app.state.test_session() as db:
        audit_event = db.scalar(
            select(SecurityEvent).where(SecurityEvent.action == AuditAction.LOGIN_FAILED)
        )
        assert audit_event is not None
        assert audit_event.result == "failure"
        assert audit_event.request_id == "failed-login-request"
        serialized = json.dumps(audit_event.event_metadata)
        assert email not in serialized
        assert wrong_password not in serialized
        assert len(audit_event.event_metadata["email_identity"]) == 64


def test_security_events_api_is_admin_only_and_paginated(client):
    register(client, "audit-list@example.com")
    assert client.get("/api/v1/security-events").status_code == 401
    assert client.post(
        "/api/v1/auth/login",
        json={"email": "audit-list@example.com", "password": PASSWORD},
    ).status_code == 200
    assert client.get("/api/v1/security-events").status_code == 403

    create_admin(client)
    assert client.post(
        "/api/v1/auth/login",
        json={"email": "audit-admin@example.com", "password": PASSWORD},
    ).status_code == 200

    response = client.get(
        f"/api/v1/security-events?action={AuditAction.USER_REGISTERED}&pageSize=1"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert body["total_items"] == 1
    assert body["items"][0]["action"] == AuditAction.USER_REGISTERED


def test_business_change_and_audit_event_roll_back_together(client):
    create_admin(client, "rollback-admin@example.com")
    with client.app.state.test_session() as db:
        user = db.scalar(select(User).where(User.email == "rollback-admin@example.com"))
        user.status = AccountStatus.BLOCKED
        record_event(
            db,
            context=CONTEXT,
            action="admin.user_blocked",
            actor_user_id=user.id,
            actor_role=user.role.value,
            object_type="user",
            object_id=user.id,
        )
        db.flush()
        db.rollback()

    with client.app.state.test_session() as db:
        user = db.scalar(select(User).where(User.email == "rollback-admin@example.com"))
        assert user.status == AccountStatus.ACTIVE
        assert db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.request_id == CONTEXT.request_id)
        ) == 0


def test_security_events_are_append_only(client):
    register(client, "immutable-audit@example.com")

    with client.app.state.test_session() as db:
        audit_event = db.scalar(select(SecurityEvent))
        audit_event.action = "tampered"
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()

    with client.app.state.test_session() as db:
        audit_event = db.scalar(select(SecurityEvent))
        db.delete(audit_event)
        with pytest.raises(ValueError, match="append-only"):
            db.commit()
        db.rollback()


def test_audit_metadata_rejects_sensitive_fields(client):
    with client.app.state.test_session() as db:
        with pytest.raises(ValueError, match="sensitive"):
            record_event(
                db,
                context=CONTEXT,
                action="test.sensitive_metadata",
                metadata={"request": {"password": "must-not-be-stored"}},
            )


def test_deleting_actor_does_not_rewrite_audit_history(client):
    create_admin(client, "deleted-audit-actor@example.com")
    with client.app.state.test_session() as db:
        user = db.scalar(select(User).where(User.email == "deleted-audit-actor@example.com"))
        actor_id = user.id
        record_event(
            db,
            context=CONTEXT,
            action="admin.test_actor_snapshot",
            actor_user_id=actor_id,
            actor_role=user.role.value,
            object_type="user",
            object_id=actor_id,
        )
        db.commit()
        db.delete(user)
        db.commit()

    with client.app.state.test_session() as db:
        audit_event = db.scalar(
            select(SecurityEvent).where(SecurityEvent.action == "admin.test_actor_snapshot")
        )
        assert audit_event.actor_user_id == actor_id
