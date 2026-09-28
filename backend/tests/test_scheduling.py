import smtplib
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.clock import utc_now
from app.contracts import EmailDeliveryCommand
from app.email.service import MAX_EMAIL_DELIVERY_ATTEMPTS, execute_email_delivery, next_retry_at
from app.outbox.models import OutboxEvent
from app.scheduler import run_iteration
from app.scheduling.email import dispatch_email_events


class FailingProducer:
    def send_task(self, name, *, args, queue):
        raise ConnectionError("broker unavailable")


def test_scheduler_continues_after_one_dispatcher_fails():
    calls = []

    def fail():
        calls.append("failed")
        raise RuntimeError("email dispatcher failed")

    def succeed():
        calls.append("succeeded")

    run_iteration((("email", fail), ("youtube", succeed)))

    assert calls == ["failed", "succeeded"]


def test_email_worker_ignores_stale_dispatch(client):
    session_factory = client.app.state.test_session
    current_dispatch_id = uuid.uuid4()
    event = OutboxEvent(
        event_type="email_delivery_requested",
        payload={
            "recipient": "stale@example.com",
            "subject": "Subject",
            "body": "Body",
        },
        state="processing",
        processing_until=utc_now() + timedelta(minutes=5),
        dispatch_id=current_dispatch_id,
    )
    with session_factory() as db:
        db.add(event)
        db.commit()
        event_id = event.id

    sent = []
    execute_email_delivery(
        EmailDeliveryCommand(
            event_id=event_id,
            dispatch_id=uuid.uuid4(),
            recipient="stale@example.com",
            subject="Subject",
            body="Body",
        ),
        session_factory=session_factory,
        sender=sent.append,
    )

    assert sent == []
    with session_factory() as db:
        stored = db.get(OutboxEvent, event_id)
        assert stored.state == "processing"
        assert stored.dispatch_id == current_dispatch_id


def test_email_broker_failure_releases_only_current_dispatch(client):
    session_factory = client.app.state.test_session
    event = OutboxEvent(
        event_type="email_delivery_requested",
        payload={
            "recipient": "retry@example.com",
            "subject": "Subject",
            "body": "Body",
        },
        state="pending",
        available_at=utc_now(),
    )
    with session_factory() as db:
        db.add(event)
        db.commit()
        event_id = event.id

    assert dispatch_email_events(
        session_factory=session_factory,
        task_producer=FailingProducer(),
    ) == 0

    with session_factory() as db:
        stored = db.scalar(select(OutboxEvent).where(OutboxEvent.id == event_id))
        assert stored.state == "pending"
        assert stored.dispatch_id is None
        assert stored.processing_until is None
        assert stored.last_error == "broker_publish_failed"


def test_email_retry_delay_is_bounded():
    now = utc_now()

    assert next_retry_at(now, 0) == now + timedelta(minutes=1)
    assert next_retry_at(now, 8) == now + timedelta(minutes=8)
    assert next_retry_at(now, 100) == now + timedelta(minutes=30)


def _processing_email_event(session_factory, *, attempt_count: int = 1):
    dispatch_id = uuid.uuid4()
    event = OutboxEvent(
        event_type="email_delivery_requested",
        payload={"recipient": "delivery@example.com", "subject": "Subject", "body": "Body"},
        state="processing",
        attempt_count=attempt_count,
        processing_until=utc_now() + timedelta(minutes=5),
        dispatch_id=dispatch_id,
    )
    with session_factory() as db:
        db.add(event)
        db.commit()
        return event.id, dispatch_id


def _delivery_command(event_id, dispatch_id):
    return EmailDeliveryCommand(
        event_id=event_id,
        dispatch_id=dispatch_id,
        recipient="delivery@example.com",
        subject="Subject",
        body="Body",
    )


def test_email_worker_marks_permanent_smtp_failure_terminal(client):
    session_factory = client.app.state.test_session
    event_id, dispatch_id = _processing_email_event(session_factory)

    def reject(_command):
        raise smtplib.SMTPDataError(550, b"message rejected")

    execute_email_delivery(
        _delivery_command(event_id, dispatch_id),
        session_factory=session_factory,
        sender=reject,
    )

    with session_factory() as db:
        stored = db.get(OutboxEvent, event_id)
        assert stored.state == "failed"
        assert stored.failed_at is not None
        assert stored.last_error == "smtp_permanent_550"


def test_email_worker_retries_transient_failure_until_limit(client):
    session_factory = client.app.state.test_session
    event_id, dispatch_id = _processing_email_event(session_factory, attempt_count=1)

    def unavailable(_command):
        raise ConnectionError("SMTP unavailable")

    execute_email_delivery(
        _delivery_command(event_id, dispatch_id),
        session_factory=session_factory,
        sender=unavailable,
    )

    with session_factory() as db:
        stored = db.get(OutboxEvent, event_id)
        assert stored.state == "pending"
        assert stored.failed_at is None
        assert stored.last_error == "ConnectionError"

    final_event_id, final_dispatch_id = _processing_email_event(
        session_factory, attempt_count=MAX_EMAIL_DELIVERY_ATTEMPTS
    )
    execute_email_delivery(
        _delivery_command(final_event_id, final_dispatch_id),
        session_factory=session_factory,
        sender=unavailable,
    )

    with session_factory() as db:
        stored = db.get(OutboxEvent, final_event_id)
        assert stored.state == "failed"
        assert stored.failed_at is not None
        assert stored.last_error == "ConnectionError"
