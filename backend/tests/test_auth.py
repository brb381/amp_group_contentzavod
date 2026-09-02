from sqlalchemy import func, select

from app.auth.models import PasswordResetToken, RefreshSession, User
from app.outbox.models import OutboxEvent
from app.contracts import EmailDeliveryCommand
from app.email.service import execute_email_delivery
from app.scheduling.email import dispatch_email_events


def register(client, email: str = "creator@example.com"):
    documents = client.get("/api/v1/legal-documents/current").json()
    return client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery-staple",
            "program_terms_document_id": documents["program_terms"]["id"],
            "program_terms_accepted": True,
            "personal_data_consent_document_id": documents["personal_data_consent"]["id"],
            "personal_data_consent_granted": True,
        },
    )


def test_registration_creates_pending_blogger(client):
    response = register(client)

    assert response.status_code == 201
    assert response.json()["email"] == "creator@example.com"
    assert response.json()["role"] == "blogger"
    assert response.json()["status"] == "email_pending"


def test_scheduler_routes_prepared_email_to_dedicated_worker(client):
    assert register(client, "mail-routing@example.com").status_code == 201

    class Producer:
        def __init__(self):
            self.messages = []

        def send_task(self, name, *, args, queue):
            self.messages.append((name, args, queue))

    producer = Producer()
    session_factory = client.app.state.test_session
    assert dispatch_email_events(
        session_factory=session_factory,
        task_producer=producer,
    ) == 1
    assert dispatch_email_events(
        session_factory=session_factory,
        task_producer=producer,
    ) == 0
    task_name, args, queue = producer.messages[0]
    assert task_name == "email.deliver"
    assert queue == "email"

    sent = []
    command = EmailDeliveryCommand.model_validate(args[0])
    execute_email_delivery(
        command,
        session_factory=session_factory,
        sender=sent.append,
    )
    execute_email_delivery(
        command,
        session_factory=session_factory,
        sender=sent.append,
    )
    assert len(sent) == 1
    assert str(sent[0].recipient) == "mail-routing@example.com"
    with session_factory() as db:
        event = db.get(OutboxEvent, command.event_id)
        assert event.state == "dispatched"
        assert "body" not in event.payload


def test_login_me_refresh_and_logout(client):
    assert register(client).status_code == 201

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "creator@example.com", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    assert "amp_access" in login.headers["set-cookie"]

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "creator@example.com"

    old_refresh = client.cookies.get("amp_refresh")
    csrf_token = client.cookies.get("amp_csrf")
    refreshed = client.post("/api/v1/auth/refresh", headers={"X-CSRF-Token": csrf_token})
    assert refreshed.status_code == 200
    assert client.cookies.get("amp_refresh") != old_refresh

    replay = client.post(
        "/api/v1/auth/refresh",
        cookies={"amp_refresh": old_refresh},
        headers={"X-CSRF-Token": client.cookies.get("amp_csrf")},
    )
    assert replay.status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_rate_limit_returns_retry_after(client):
    assert register(client, "limited@example.com").status_code == 201

    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "limited@example.com", "password": "wrong-password"},
        )
        assert response.status_code == 401

    limited = client.post(
        "/api/v1/auth/login",
        json={"email": "limited@example.com", "password": "wrong-password"},
    )
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "900"
    assert limited.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


def test_password_reset_is_private_one_time_and_revokes_sessions(client):
    email = "password-reset@example.com"
    old_password = "correct-horse-battery-staple"
    new_password = "new-correct-horse-battery-staple"
    assert register(client, email).status_code == 201
    assert client.post(
        "/api/v1/auth/login", json={"email": email, "password": old_password}
    ).status_code == 200

    existing = client.post("/api/v1/auth/password-reset-requests", json={"email": email})
    missing = client.post(
        "/api/v1/auth/password-reset-requests", json={"email": "missing-user@example.com"}
    )
    assert existing.status_code == missing.status_code == 202
    assert existing.content == missing.content

    with client.app.state.test_session() as db:
        user = db.scalar(select(User).where(User.email == email))
        reset_event = next(
            (
                event
                for event in db.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.event_type == "email_delivery_requested"
                    )
                )
                if event.payload.get("recipient") == email
                and "reset-password" in event.payload.get("body", "")
            ),
            None,
        )
        assert reset_event is not None
        raw_token = reset_event.payload["body"].split("#token=", 1)[1]

    reset = client.post(
        "/api/v1/auth/password-resets",
        json={"token": raw_token, "new_password": new_password},
    )
    assert reset.status_code == 204
    assert client.post(
        "/api/v1/auth/login", json={"email": email, "password": old_password}
    ).status_code == 401
    assert client.post(
        "/api/v1/auth/login", json={"email": email, "password": new_password}
    ).status_code == 200

    replay = client.post(
        "/api/v1/auth/password-resets",
        json={"token": raw_token, "new_password": "another-valid-password"},
    )
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "INVALID_OR_EXPIRED_TOKEN"

    with client.app.state.test_session() as db:
        assert db.scalar(
            select(func.count())
            .select_from(RefreshSession)
            .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        ) == 1
        assert db.scalar(select(func.count()).select_from(PasswordResetToken)) == 1


def test_password_reset_request_rate_limit(client):
    for _ in range(3):
        assert client.post(
            "/api/v1/auth/password-reset-requests", json={"email": "limited-reset@example.com"}
        ).status_code == 202

    limited = client.post(
        "/api/v1/auth/password-reset-requests", json={"email": "limited-reset@example.com"}
    )
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "3600"
