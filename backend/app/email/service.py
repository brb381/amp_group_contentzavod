import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import select

from app.clock import utc_now
from app.contracts import EmailDeliveryCommand
from app.email_config import EmailWorkerSettings
from app.outbox.models import OutboxEvent


def next_retry_at(now: datetime, attempt_count: int) -> datetime:
    return now + timedelta(minutes=min(30, max(1, attempt_count)))


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def send_email(command: EmailDeliveryCommand, settings: EmailWorkerSettings) -> None:
    message = EmailMessage()
    message["Subject"] = command.subject
    message["From"] = settings.smtp_from_email
    message["To"] = str(command.recipient)
    message.set_content(command.body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_username and settings.smtp_password:
            smtp.login(settings.smtp_username, settings.smtp_password)
        smtp.send_message(message)


def execute_email_delivery(
    command: EmailDeliveryCommand,
    *,
    settings: EmailWorkerSettings | None = None,
    session_factory,
    sender=None,
) -> None:
    if sender is None:
        if settings is None:
            raise ValueError("Email settings are required when no sender is provided")

        def sender(payload: EmailDeliveryCommand) -> None:
            send_email(payload, settings)
    now = utc_now()
    db = session_factory()
    try:
        event = db.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.id == command.event_id)
            .with_for_update()
        )
        if (
            not event
            or event.state != "processing"
            or event.dispatch_id != command.dispatch_id
            or event.processing_until is None
            or _aware(event.processing_until) < now
        ):
            db.rollback()
            return
        event.state = "delivering"
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    try:
        sender(command)
    except Exception as error:
        db = session_factory()
        try:
            event = db.get(OutboxEvent, command.event_id)
            if (
                event
                and event.state == "delivering"
                and event.dispatch_id == command.dispatch_id
            ):
                event.state = "pending"
                event.processing_until = None
                event.dispatch_id = None
                event.available_at = next_retry_at(utc_now(), event.attempt_count)
                event.last_error = type(error).__name__
                db.commit()
        finally:
            db.close()
        return

    db = session_factory()
    try:
        event = db.get(OutboxEvent, command.event_id)
        if (
            event
            and event.state == "delivering"
            and event.dispatch_id == command.dispatch_id
        ):
            event.state = "dispatched"
            event.dispatched_at = utc_now()
            event.processing_until = None
            event.last_error = None
            event.payload = {
                "recipient": str(command.recipient),
                "subject": command.subject,
            }
            db.commit()
    finally:
        db.close()
