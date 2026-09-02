import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.audit.service import AuditAction, record_event
from app.auth.dependencies import CurrentUser, require_active_roles, require_csrf
from app.auth.models import Role, User
from app.database.session import get_db
from app.notifications.models import NotificationChannel, NotificationSeverity
from app.notifications.schemas import (
    NotificationListResponse,
    NotificationReadAllResponse,
    NotificationResponse,
    NotificationTemplateListResponse,
    NotificationTemplateVersionCreateRequest,
    NotificationTemplateVersionResponse,
    NotificationUnreadCountResponse,
)
from app.notifications.service import (
    create_template_version,
    get_unread_count,
    list_templates,
    list_my_notifications,
    mark_all_notifications_read,
    mark_notification_read,
)


def _set_private_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(tags=["notifications"], dependencies=[Depends(_set_private_no_store)])
Admin = Annotated[User, Depends(require_active_roles(Role.ADMIN))]


@router.get("/me/notifications", response_model=NotificationListResponse)
def get_my_notifications(
    actor: CurrentUser,
    unread_only: bool = Query(default=False, alias="unreadOnly"),
    template_code: str | None = Query(default=None, alias="templateCode", max_length=100),
    severity: NotificationSeverity | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> NotificationListResponse:
    return list_my_notifications(
        db,
        actor=actor,
        unread_only=unread_only,
        template_code=template_code,
        severity=severity,
        page=page,
        page_size=page_size,
    )


@router.get("/me/notifications/unread-count", response_model=NotificationUnreadCountResponse)
def get_my_notification_unread_count(
    actor: CurrentUser,
    db: Session = Depends(get_db, scope="function"),
) -> NotificationUnreadCountResponse:
    return get_unread_count(db, actor=actor)


@router.post("/me/notifications/{notification_id}/reads", response_model=NotificationResponse)
def read_notification(
    notification_id: uuid.UUID,
    actor: CurrentUser,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> NotificationResponse:
    return mark_notification_read(db, actor=actor, notification_id=notification_id)


@router.post("/me/notifications/read-all", response_model=NotificationReadAllResponse)
def read_all_notifications(
    actor: CurrentUser,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> NotificationReadAllResponse:
    return mark_all_notifications_read(db, actor=actor)


@router.get("/admin/notification-templates", response_model=NotificationTemplateListResponse)
def get_notification_templates(
    actor: Admin,
    include_inactive: bool = Query(default=False, alias="includeInactive"),
    db: Session = Depends(get_db, scope="function"),
) -> NotificationTemplateListResponse:
    del actor
    return list_templates(db, include_inactive=include_inactive)


@router.post(
    "/admin/notification-templates/{code}/{channel}/versions",
    response_model=NotificationTemplateVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_notification_template_version(
    code: str,
    channel: NotificationChannel,
    payload: NotificationTemplateVersionCreateRequest,
    request: Request,
    actor: Admin,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> NotificationTemplateVersionResponse:
    template = create_template_version(
        db,
        actor=actor,
        code=code,
        channel=channel,
        payload=payload,
    )
    record_event(
        db,
        context=context_from_request(request),
        action=AuditAction.NOTIFICATION_TEMPLATE_UPDATED,
        actor_user_id=actor.id,
        actor_role=actor.role.value,
        object_type="notification_template_version",
        object_id=template.id,
        metadata={"code": code, "channel": channel.value, "version": template.version},
    )
    return template
