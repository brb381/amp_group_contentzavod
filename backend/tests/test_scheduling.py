import uuid
from datetime import timedelta

from sqlalchemy import select

from app.clock import utc_now
from app.contracts import EmailDeliveryCommand
from app.email.service import execute_email_delivery, next_retry_at
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
