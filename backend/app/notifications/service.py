import math
import string
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.models import User
from app.clock import utc_now
from app.errors import APIError
from app.notifications.models import (
    Notification,
    NotificationChannel,
    NotificationSeverity,
    NotificationTemplateVersion,
)
from app.notifications.schemas import (
    NotificationListResponse,
    NotificationReadAllResponse,
    NotificationTemplateVersionCreateRequest,
    NotificationTemplateListResponse,
    NotificationUnreadCountResponse,
)
from app.outbox.models import OutboxEvent


@dataclass(frozen=True)
class NotificationCommand:
    recipient_user_id: uuid.UUID
    template_code: str
    context: dict[str, str | int]
    severity: NotificationSeverity
    deduplication_key: str
    related_object_type: str | None = None
    related_object_id: uuid.UUID | None = None
    action_path: str | None = None
    send_email: bool = True
    expires_at: datetime | None = None


def _active_template(
    db: Session, code: str, channel: NotificationChannel
) -> NotificationTemplateVersion:
    template = db.scalar(
        select(NotificationTemplateVersion).where(
            NotificationTemplateVersion.code == code,
            NotificationTemplateVersion.channel == channel,
            NotificationTemplateVersion.is_active.is_(True),
        )
    )
    if not template:
        raise RuntimeError(f"Active notification template is missing: {code}/{channel.value}")
    return template


def _render(template: NotificationTemplateVersion, value: str, context: dict[str, str | int]) -> str:
    formatter = string.Template(value)
    identifiers = set(formatter.get_identifiers())
    allowed = set(template.allowed_variables)
    if identifiers - allowed or identifiers - context.keys() or context.keys() - allowed:
        raise RuntimeError(f"Invalid notification context for template {template.code}")
    rendered = formatter.substitute({key: str(item) for key, item in context.items()}).strip()
    if not rendered:
        raise RuntimeError(f"Notification template rendered an empty value: {template.code}")
    return rendered


def create_notification(db: Session, command: NotificationCommand) -> Notification:
    existing = db.scalar(
        select(Notification).where(
            Notification.recipient_user_id == command.recipient_user_id,
            Notification.deduplication_key == command.deduplication_key,
        )
    )
    if existing:
        return existing

    recipient = db.get(User, command.recipient_user_id)
    if not recipient:
        raise RuntimeError("Notification recipient does not exist")
    in_app_template = _active_template(
        db, command.template_code, NotificationChannel.IN_APP
    )
    title = _render(
        in_app_template,
        in_app_template.title_template or "",
        command.context,
    )
    body = _render(in_app_template, in_app_template.body_template, command.context)
    notification = Notification(
        recipient_user_id=recipient.id,
        template_code=command.template_code,
        template_version_id=in_app_template.id,
        severity=command.severity,
        title=title,
        body=body,
        related_object_type=command.related_object_type,
        related_object_id=command.related_object_id,
        action_path=command.action_path,
        deduplication_key=command.deduplication_key,
        expires_at=command.expires_at,
    )
    try:
        with db.begin_nested():
            db.add(notification)
            db.flush([notification])
    except IntegrityError:
        existing = db.scalar(
            select(Notification).where(
                Notification.recipient_user_id == command.recipient_user_id,
                Notification.deduplication_key == command.deduplication_key,
            )
        )
        if existing:
            return existing
        raise

    if command.send_email:
        email_template = _active_template(
            db, command.template_code, NotificationChannel.EMAIL
        )
        subject = _render(
            email_template,
            email_template.subject_template or "",
            command.context,
        )
        email_body = _render(email_template, email_template.body_template, command.context)
        db.add(
            OutboxEvent(
                event_type="email_delivery_requested",
                payload={
                    "recipient": recipient.email,
                    "subject": subject,
                    "body": email_body,
                },
                correlation_type="notification",
                correlation_id=notification.id,
            )
        )
    return notification


def list_my_notifications(
    db: Session,
    *,
    actor: User,
    unread_only: bool,
    template_code: str | None,
    severity: NotificationSeverity | None,
    page: int,
    page_size: int,
) -> NotificationListResponse:
    conditions = [Notification.recipient_user_id == actor.id]
    if unread_only:
        conditions.append(Notification.read_at.is_(None))
    if template_code:
        conditions.append(Notification.template_code == template_code)
    if severity:
        conditions.append(Notification.severity == severity)
    total = db.scalar(select(func.count()).select_from(Notification).where(*conditions)) or 0
    items = list(
        db.scalars(
            select(Notification)
            .where(*conditions)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return NotificationListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size) if total else 0,
    )


def get_unread_count(db: Session, *, actor: User) -> NotificationUnreadCountResponse:
    count = db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.recipient_user_id == actor.id,
            Notification.read_at.is_(None),
        )
    ) or 0
    return NotificationUnreadCountResponse(unread_count=count)


def mark_notification_read(
    db: Session, *, actor: User, notification_id: uuid.UUID
) -> Notification:
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.recipient_user_id == actor.id,
        )
    )
    if not notification:
        raise APIError(404, "NOTIFICATION_NOT_FOUND", "Notification was not found")
    if notification.read_at is None:
        notification.read_at = utc_now()
        db.flush()
    return notification


def mark_all_notifications_read(
    db: Session, *, actor: User
) -> NotificationReadAllResponse:
    result = db.execute(
        update(Notification)
        .where(
            Notification.recipient_user_id == actor.id,
            Notification.read_at.is_(None),
        )
        .values(read_at=utc_now())
    )
    return NotificationReadAllResponse(updated_count=result.rowcount)


def list_templates(
    db: Session, *, include_inactive: bool
) -> NotificationTemplateListResponse:
    statement = select(NotificationTemplateVersion)
    if not include_inactive:
        statement = statement.where(NotificationTemplateVersion.is_active.is_(True))
    items = list(
        db.scalars(
            statement.order_by(
                NotificationTemplateVersion.code,
                NotificationTemplateVersion.channel,
                NotificationTemplateVersion.version.desc(),
            )
        )
    )
    return NotificationTemplateListResponse(items=items)


def create_template_version(
    db: Session,
    *,
    actor: User,
    code: str,
    channel: NotificationChannel,
    payload: NotificationTemplateVersionCreateRequest,
) -> NotificationTemplateVersion:
    current = db.scalar(
        select(NotificationTemplateVersion)
        .where(
            NotificationTemplateVersion.code == code,
            NotificationTemplateVersion.channel == channel,
            NotificationTemplateVersion.is_active.is_(True),
        )
        .with_for_update()
    )
    if not current:
        raise APIError(404, "NOTIFICATION_TEMPLATE_NOT_FOUND", "Template was not found")
    if channel == NotificationChannel.IN_APP:
        if payload.title_template is None or payload.subject_template is not None:
            raise APIError(422, "INVALID_TEMPLATE_FIELDS", "In-app template requires title only")
    elif payload.subject_template is None or payload.title_template is not None:
        raise APIError(422, "INVALID_TEMPLATE_FIELDS", "Email template requires subject only")
    if payload.subject_template and ("\r" in payload.subject_template or "\n" in payload.subject_template):
        raise APIError(422, "INVALID_TEMPLATE_FIELDS", "Email subject must be a single line")
    if (
        current.title_template == payload.title_template
        and current.subject_template == payload.subject_template
        and current.body_template == payload.body_template
        and sorted(current.allowed_variables) == sorted(payload.allowed_variables)
    ):
        return current

    probe = NotificationTemplateVersion(
        code=code,
        channel=channel,
        version=current.version + 1,
        title_template=payload.title_template,
        subject_template=payload.subject_template,
        body_template=payload.body_template,
        allowed_variables=payload.allowed_variables,
        created_by_user_id=actor.id,
    )
    for value in (payload.title_template, payload.subject_template, payload.body_template):
        if value:
            identifiers = set(string.Template(value).get_identifiers())
            if identifiers - set(payload.allowed_variables):
                raise APIError(
                    422,
                    "INVALID_TEMPLATE_VARIABLES",
                    "Template contains a variable that is not allowed",
                )
    current.is_active = False
    db.add(probe)
    db.flush()
    return probe
