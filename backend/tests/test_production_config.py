from email.message import EmailMessage

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.email_config import EmailWorkerSettings
from app.monitor import MonitorSettings
from app import smtp


API_SETTINGS = {
    "database_url": "sqlite+pysqlite:///:memory:",
    "jwt_secret": "A" * 64,
    "redis_url": "redis://localhost:6379/0",
    "rate_limit_redis_url": "redis://localhost:6379/1",
    "frontend_url": "https://app.example.com",
    "cookie_secure": True,
}


def test_production_api_rejects_insecure_defaults():
    Settings(environment="production", **API_SETTINGS)
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(environment="production", **{**API_SETTINGS, "cookie_secure": False})
    with pytest.raises(ValidationError, match="FRONTEND_URL"):
        Settings(environment="production", **{**API_SETTINGS, "frontend_url": "http://app.example.com"})
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(
            environment="production",
            **{**API_SETTINGS, "jwt_secret": "local-development-secret-" + "A" * 48},
        )


def test_production_email_requires_real_encrypted_smtp():
    base = {
        "environment": "production",
        "database_url": "sqlite+pysqlite:///:memory:",
        "redis_url": "redis://localhost:6379/0",
        "smtp_host": "smtp.example.com",
        "smtp_port": 465,
        "smtp_from_email": "alerts@example.com",
        "smtp_use_tls": False,
        "smtp_use_ssl": True,
    }
    EmailWorkerSettings(**base)
    with pytest.raises(ValidationError, match="SMTP_HOST"):
        EmailWorkerSettings(**{**base, "smtp_host": "mailpit"})
    with pytest.raises(ValidationError, match="STARTTLS or SMTP_SSL"):
        EmailWorkerSettings(**{**base, "smtp_use_ssl": False})
    with pytest.raises(ValidationError, match="cannot both"):
        EmailWorkerSettings(**{**base, "smtp_use_tls": True})
    with pytest.raises(ValidationError, match="must be set together"):
        EmailWorkerSettings(**{**base, "smtp_username": "user"})


def test_production_monitor_requires_real_encrypted_smtp():
    with pytest.raises(ValidationError, match="SMTP_HOST"):
        MonitorSettings(
            environment="production",
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost:6379/0",
            monitor_alert_email="ops@example.com",
            smtp_host="mailpit",
            smtp_from_email="alerts@example.com",
            smtp_use_tls=True,
        )


def test_production_monitor_rejects_test_recipient():
    with pytest.raises(ValidationError, match="MONITOR_ALERT_EMAIL"):
        MonitorSettings(
            environment="production",
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost:6379/0",
            monitor_alert_email="admin@example.test",
            smtp_host="smtp.example.com",
            smtp_from_email="alerts@example.com",
            smtp_use_tls=True,
        )


def test_smtp_uses_implicit_tls_or_starttls(monkeypatch):
    events = []

    class FakeClient:
        def __init__(self, host, port, *, timeout):
            events.append(("connect", host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def starttls(self):
            events.append("starttls")

        def login(self, username, password):
            events.append(("login", username, password))

        def send_message(self, _message):
            events.append("send")

    monkeypatch.setattr(smtp.smtplib, "SMTP", FakeClient)
    monkeypatch.setattr(smtp.smtplib, "SMTP_SSL", FakeClient)
    message = EmailMessage()
    message.set_content("test")

    smtp.send_smtp_message(
        message,
        host="smtp.example.com",
        port=587,
        use_tls=True,
        use_ssl=False,
        username="user",
        password="secret",
        timeout=3,
    )
    assert events == [
        ("connect", "smtp.example.com", 587, 3),
        "starttls",
        ("login", "user", "secret"),
        "send",
    ]

    events.clear()
    smtp.send_smtp_message(
        message,
        host="smtp.example.com",
        port=465,
        use_tls=False,
        use_ssl=True,
        username=None,
        password=None,
        timeout=3,
    )
    assert events == [("connect", "smtp.example.com", 465, 3), "send"]
