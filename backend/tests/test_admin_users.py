import pytest
from sqlalchemy import func, select

import app.auth.admin_service as admin_service
from app.audit.models import SecurityEvent
from app.audit.service import AuditAction, AuditContext
from app.auth.bootstrap import bootstrap_first_admin
from app.auth.models import AccountStatus, RefreshSession, Role, User
from app.auth.security import hash_password, utc_now
from app.errors import APIError


PASSWORD = "correct-horse-battery-staple"


def register(client, email: str) -> User:
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
    with client.app.state.test_session() as db:
        return db.scalar(select(User).where(User.email == email))


def activate_user(client, email: str) -> User:
    with client.app.state.test_session() as db:
        user = db.scalar(select(User).where(User.email == email))
        user.status = AccountStatus.ACTIVE
        user.email_verified_at = utc_now()
        db.commit()
        return user


def create_admin(client, email: str = "admin-users@example.com") -> User:
    with client.app.state.test_session() as db:
        admin = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=Role.ADMIN,
            status=AccountStatus.ACTIVE,
            email_verified_at=utc_now(),
        )
        db.add(admin)
        db.commit()
        return admin


def login(client, email: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 200


def csrf_headers(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("amp_csrf")}


def test_admin_user_list_requires_active_administrator_and_filters(client):
    register(client, "ordinary-user@example.com")
    login(client, "ordinary-user@example.com")
    assert client.get("/api/v1/admin/users").status_code == 403

    create_admin(client)
    login(client, "admin-users@example.com")
    response = client.get(
        "/api/v1/admin/users?email=ordinary&role=blogger&status=email_pending&pageSize=1"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_items"] == 1
    assert body["page_size"] == 1
    assert body["items"][0]["email"] == "ordinary-user@example.com"


def test_role_change_revokes_sessions_and_writes_audit(client):
    target = register(client, "role-target@example.com")
    activate_user(client, target.email)
    login(client, target.email)
    with client.app.state.test_session() as db:
        assert db.scalar(
            select(func.count())
            .select_from(RefreshSession)
            .where(RefreshSession.user_id == target.id, RefreshSession.revoked_at.is_(None))
        ) == 1

    create_admin(client)
    login(client, "admin-users@example.com")
    response = client.put(
        f"/api/v1/admin/users/{target.id}/role",
        json={"role": "moderator", "reason": "Assigned to content moderation"},
        headers=csrf_headers(client),
    )

    assert response.status_code == 200
    assert response.json()["role"] == "moderator"
    with client.app.state.test_session() as db:
        assert db.scalar(
            select(func.count())
            .select_from(RefreshSession)
            .where(RefreshSession.user_id == target.id, RefreshSession.revoked_at.is_(None))
        ) == 0
        audit_event = db.scalar(
            select(SecurityEvent).where(
                SecurityEvent.action == AuditAction.USER_ROLE_CHANGED,
                SecurityEvent.object_id == target.id,
            )
        )
        assert audit_event.event_metadata["old_role"] == "blogger"
        assert audit_event.event_metadata["new_role"] == "moderator"


def test_block_and_unblock_preserve_previous_status_and_do_not_restore_sessions(client):
    target = register(client, "blocked-target@example.com")
    login(client, target.email)
    create_admin(client)
    login(client, "admin-users@example.com")

    blocked = client.patch(
        f"/api/v1/admin/users/{target.id}/access",
        json={"is_blocked": True, "reason": "Suspicious registration activity"},
        headers=csrf_headers(client),
    )

    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["status_before_block"] == "email_pending"
    assert client.post(
        "/api/v1/auth/login",
        json={"email": target.email, "password": PASSWORD},
    ).status_code == 403

    unblocked = client.patch(
        f"/api/v1/admin/users/{target.id}/access",
        json={"is_blocked": False, "reason": "Manual review completed"},
        headers=csrf_headers(client),
    )
    assert unblocked.status_code == 200
    assert unblocked.json()["status"] == "email_pending"
    assert unblocked.json()["status_before_block"] is None

    with client.app.state.test_session() as db:
        assert db.scalar(
            select(func.count())
            .select_from(RefreshSession)
            .where(RefreshSession.user_id == target.id, RefreshSession.revoked_at.is_(None))
        ) == 0


def test_administrator_cannot_change_own_role_or_access(client):
    admin = create_admin(client)
    login(client, admin.email)

    role_change = client.put(
        f"/api/v1/admin/users/{admin.id}/role",
        json={"role": "moderator", "reason": "Unsafe self demotion"},
        headers=csrf_headers(client),
    )
    access_change = client.patch(
        f"/api/v1/admin/users/{admin.id}/access",
        json={"is_blocked": True, "reason": "Unsafe self blocking"},
        headers=csrf_headers(client),
    )

    assert role_change.status_code == 409
    assert role_change.json()["error"]["code"] == "SELF_ROLE_CHANGE_NOT_ALLOWED"
    assert access_change.status_code == 409
    assert access_change.json()["error"]["code"] == "SELF_BLOCK_NOT_ALLOWED"


def test_staff_role_requires_active_verified_account(client):
    target = register(client, "pending-staff@example.com")
    create_admin(client)
    login(client, "admin-users@example.com")

    response = client.put(
        f"/api/v1/admin/users/{target.id}/role",
        json={"role": "finance", "reason": "Finance department account"},
        headers=csrf_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "STAFF_ACCOUNT_NOT_ACTIVE"


def test_admin_change_rolls_back_user_sessions_and_audit_together(client, monkeypatch):
    target = register(client, "rollback-role@example.com")
    activate_user(client, target.email)
    login(client, target.email)
    create_admin(client)
    login(client, "admin-users@example.com")

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(admin_service, "record_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        client.put(
            f"/api/v1/admin/users/{target.id}/role",
            json={"role": "manager", "reason": "This transaction must roll back"},
            headers=csrf_headers(client),
        )

    with client.app.state.test_session() as db:
        user = db.get(User, target.id)
        assert user.role == Role.BLOGGER
        assert db.scalar(
            select(func.count())
            .select_from(RefreshSession)
            .where(RefreshSession.user_id == target.id, RefreshSession.revoked_at.is_(None))
        ) == 1
        assert db.scalar(
            select(func.count())
            .select_from(SecurityEvent)
            .where(SecurityEvent.action == AuditAction.USER_ROLE_CHANGED)
        ) == 0


def test_bootstrap_creates_only_first_administrator(client):
    with client.app.state.test_session() as db:
        admin = bootstrap_first_admin(
            db,
            email="first-admin@example.com",
            password=PASSWORD,
        )
        db.commit()
        assert admin.role == Role.ADMIN
        assert admin.status == AccountStatus.ACTIVE

    with client.app.state.test_session() as db:
        with pytest.raises(APIError) as error:
            bootstrap_first_admin(
                db,
                email="second-admin@example.com",
                password=PASSWORD,
            )
        assert error.value.code == "ADMIN_ALREADY_EXISTS"


def test_stale_administrator_cannot_complete_a_user_change(client):
    stale_admin = create_admin(client, "stale-admin@example.com")
    target = register(client, "stale-admin-target@example.com")
    activate_user(client, target.email)

    with client.app.state.test_session() as db:
        current_admin = db.get(User, stale_admin.id)
        current_admin.role = Role.MODERATOR
        db.commit()

    with client.app.state.test_session() as db:
        with pytest.raises(APIError) as error:
            admin_service.change_user_role(
                db,
                actor=stale_admin,
                target_user_id=target.id,
                new_role=Role.MANAGER,
                reason="Stale authorization must not be accepted",
                audit_context=AuditContext(
                    request_id="stale-admin-test",
                    ip_address="127.0.0.1",
                    user_agent="pytest",
                ),
            )
        assert error.value.code == "ADMIN_PERMISSION_CHANGED"
