import math
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import SecurityEvent
from app.audit.schemas import SecurityEventListResponse, SecurityEventResponse
from app.auth.dependencies import require_active_roles
from app.auth.models import Role, User
from app.database.session import get_db
from app.errors import APIError


router = APIRouter(prefix="/security-events", tags=["security events"])
Administrator = Annotated[User, Depends(require_active_roles(Role.ADMIN))]


@router.get("", response_model=SecurityEventListResponse)
def list_security_events(
    _: Administrator,
    action: str | None = Query(default=None, max_length=96),
    result: str | None = Query(default=None, pattern="^(success|failure|denied)$"),
    actor_user_id: uuid.UUID | None = Query(default=None, alias="actorId"),
    object_type: str | None = Query(default=None, alias="objectType", max_length=64),
    object_id: uuid.UUID | None = Query(default=None, alias="objectId"),
    request_id: str | None = Query(default=None, alias="requestId", max_length=128),
    date_from: datetime | None = Query(default=None, alias="dateFrom"),
    date_to: datetime | None = Query(default=None, alias="dateTo"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> SecurityEventListResponse:
    if date_from and date_to and date_from > date_to:
        raise APIError(422, "INVALID_DATE_RANGE", "dateFrom must not be later than dateTo")
    filters = []
    if action:
        filters.append(SecurityEvent.action == action)
    if result:
        filters.append(SecurityEvent.result == result)
    if actor_user_id:
        filters.append(SecurityEvent.actor_user_id == actor_user_id)
    if object_type:
        filters.append(SecurityEvent.object_type == object_type)
    if object_id:
        filters.append(SecurityEvent.object_id == object_id)
    if request_id:
        filters.append(SecurityEvent.request_id == request_id)
    if date_from:
        filters.append(SecurityEvent.occurred_at >= date_from)
    if date_to:
        filters.append(SecurityEvent.occurred_at <= date_to)

    total = db.scalar(select(func.count()).select_from(SecurityEvent).where(*filters)) or 0
    events = list(
        db.scalars(
            select(SecurityEvent)
            .where(*filters)
            .order_by(SecurityEvent.occurred_at.desc(), SecurityEvent.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return SecurityEventListResponse(
        items=events,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )


@router.get("/{event_id}", response_model=SecurityEventResponse)
def get_security_event(
    event_id: uuid.UUID, _: Administrator, db: Session = Depends(get_db, scope="function")
) -> SecurityEvent:
    audit_event = db.get(SecurityEvent, event_id)
    if not audit_event:
        raise APIError(404, "SECURITY_EVENT_NOT_FOUND", "Security event was not found")
    return audit_event
