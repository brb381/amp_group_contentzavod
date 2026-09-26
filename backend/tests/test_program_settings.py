from sqlalchemy import select

from app.audit.models import SecurityEvent
from app.audit.service import AuditAction
from app.auth.models import AccountStatus, Role, User
from app.auth.security import hash_password, utc_now


PASSWORD = "correct-horse-battery-staple"


def create_user(client, email: str, role: Role) -> User:
    with client.app.state.test_session() as db:
        user = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=role,
            status=AccountStatus.ACTIVE,
            email_verified_at=utc_now(),
        )
        db.add(user)
        db.commit()
        return user


def login(client, user: User) -> None:
    response = client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
    assert response.status_code == 200


def csrf(client) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("amp_csrf")}


def test_program_settings_are_readable_and_only_admin_can_update(client):
    blogger = create_user(client, "program-settings-blogger@example.com", Role.BLOGGER)
    login(client, blogger)
    default = client.get("/api/v1/program-settings")
    assert default.status_code == 200
    assert default.json()["program_name"] == "AMP Content Factory"

    payload = {
        **default.json(),
        "program_name": "AMP Creator Program",
        "manager_name": "Валерия",
        "manager_email": "manager@ampgroup.pro",
        "manager_telegram_url": "https://t.me/amp_manager",
        "suspicious_growth_threshold": 750000,
        "random_review_percent": 15,
        "rejection_reasons": ["Неверный товар", "Нарушены требования к ролику"],
    }
    payload.pop("updated_by_user_id", None)
    payload.pop("updated_at", None)
    forbidden = client.put("/api/v1/program-settings", json=payload, headers=csrf(client))
    assert forbidden.status_code == 403

    admin = create_user(client, "program-settings-admin@example.com", Role.ADMIN)
    login(client, admin)
    updated = client.put("/api/v1/program-settings", json=payload, headers=csrf(client))
    assert updated.status_code == 200, updated.text
    assert updated.json()["manager_telegram_url"] == "https://t.me/amp_manager"
    assert updated.json()["updated_by_user_id"] == str(admin.id)

    repeated = client.get("/api/v1/program-settings")
    assert repeated.json()["random_review_percent"] == 15
    with client.app.state.test_session() as db:
        assert db.scalar(
            select(SecurityEvent).where(SecurityEvent.action == AuditAction.PROGRAM_SETTINGS_UPDATED)
        ) is not None