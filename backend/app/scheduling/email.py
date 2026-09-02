import logging
import uuid
from datetime import datetime, timedelta

from celery import Celery
from pydantic import ValidationError
from sqlalchemy import or_, select

from app.clock import utc_now
from app.contracts import EMAIL_QUEUE, EMAIL_TASK, EmailDeliveryCommand
from app.database.factory import create_session_factory
from app.outbox.models import OutboxEvent
from app.scheduler_config import get_scheduler_settings


logger = logging.getLogger(__name__)
settings = get_scheduler_settings()
SessionLocal = create_session_factory(settings.database_url)
producer = Celery("amp_email_scheduler", broker=settings.redis_url)
EMAIL_LEASE = timedelta(minutes=5)


def dispatch_email_events(
    *,
    now: datetime | None = None,
    session_factory=SessionLocal,
    task_producer=producer,
) -> int:
    now = now or utc_now()
    db = session_factory()
    commands: list[EmailDeliveryCommand] = []
    try:
        events = list(
            db.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type == "email_delivery_requested",
                    or_(
                        (OutboxEvent.state == "pending") & (OutboxEvent.available_at <= now),
                        (OutboxEvent.state.in_(("processing", "delivering")))
                        & (OutboxEvent.processing_until < now),
                    ),
                )
                .order_by(OutboxEvent.created_at)
                .limit(20)
                .with_for_update(skip_locked=True)
            )
        )
        for event in events:
            dispatch_id = uuid.uuid4()
            try:
                command = EmailDeliveryCommand(
                    event_id=event.id,
                    dispatch_id=dispatch_id,
                    **event.payload,
                )
            except (ValidationError, TypeError):
                event.state = "failed"
                event.processing_until = None
                event.dispatch_id = None
                event.last_error = "invalid_email_command"
                continue
            event.state = "processing"
            event.processing_until = now + EMAIL_LEASE
            event.dispatch_id = dispatch_id
            event.attempt_count += 1
            commands.append(command)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    sent = 0
    for command in commands:
        try:
            task_producer.send_task(
                EMAIL_TASK,
                args=[command.model_dump(mode="json")],
                queue=EMAIL_QUEUE,
            )
            sent += 1
        except Exception:
            db = session_factory()
            try:
                event = db.get(OutboxEvent, command.event_id)
                if (
                    event
                    and event.state == "processing"
                    and event.dispatch_id == command.dispatch_id
                ):
                    event.state = "pending"
                    event.processing_until = None
                    event.dispatch_id = None
                    event.last_error = "broker_publish_failed"
                    db.commit()
            finally:
                db.close()
            logger.exception(
                "Could not publish email command",
                extra={
                    "event_id": str(command.event_id),
                    "dispatch_id": str(command.dispatch_id),
                },
            )
    return sent
