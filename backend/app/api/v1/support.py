import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import require_active_roles, require_csrf, require_roles
from app.auth.models import Role, User
from app.auth.rate_limit import RedisRateLimiter, enforce_rate_limit, get_rate_limiter
from app.database.session import get_db
from app.support.models import SupportCategory, SupportStatus
from app.support.schemas import (
    MySupportMessageResponse,
    MySupportTicketDetailResponse,
    MySupportTicketListResponse,
    MySupportTicketResponse,
    StaffSupportEventResponse,
    StaffSupportMessageResponse,
    StaffSupportTicketDetailResponse,
    StaffSupportTicketListResponse,
    SupportAssignmentRequest,
    SupportMessageCreateRequest,
    SupportStatusTransitionRequest,
    SupportTicketCreateRequest,
    RecoveryDecisionRequest,
)
from app.lifecycle.recovery import decide_account_recovery
from app.support.service import (
    assign_ticket,
    create_my_ticket,
    get_my_ticket,
    get_staff_ticket,
    list_my_tickets,
    list_staff_tickets,
    my_message_response,
    post_ticket_message,
    staff_event_response,
    staff_message_response,
    transition_ticket_status,
)


def _set_private_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(tags=["support"], dependencies=[Depends(_set_private_no_store)])
BloggerViewer = Annotated[User, Depends(require_roles(Role.BLOGGER))]
SupportStaff = Annotated[
    User, Depends(require_active_roles(Role.MODERATOR, Role.MANAGER, Role.ADMIN))
]
RecoveryReviewer = Annotated[
    User, Depends(require_active_roles(Role.MODERATOR, Role.ADMIN))
]


@router.post(
    "/me/support-tickets",
    response_model=MySupportTicketResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_my_support_ticket(
    payload: SupportTicketCreateRequest,
    request: Request,
    blogger: BloggerViewer,
    _: None = Depends(require_csrf),
    limiter: RedisRateLimiter = Depends(get_rate_limiter),
    db: Session = Depends(get_db, scope="function"),
) -> MySupportTicketResponse:
    enforce_rate_limit(
        limiter,
        key=f"rate-limit:support:create:{blogger.id}",
        limit=10,
        window_seconds=3_600,
        fail_closed=False,
    )
    return create_my_ticket(
        db,
        actor=blogger,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.get("/me/support-tickets", response_model=MySupportTicketListResponse)
def get_my_support_tickets(
    blogger: BloggerViewer,
    ticket_status: SupportStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> MySupportTicketListResponse:
    return list_my_tickets(
        db, actor=blogger, status=ticket_status, page=page, page_size=page_size
    )


@router.get("/me/support-tickets/{ticket_id}", response_model=MySupportTicketDetailResponse)
def get_my_support_ticket(
    ticket_id: uuid.UUID,
    blogger: BloggerViewer,
    db: Session = Depends(get_db, scope="function"),
) -> MySupportTicketDetailResponse:
    return get_my_ticket(db, actor=blogger, ticket_id=ticket_id)


@router.post(
    "/me/support-tickets/{ticket_id}/messages",
    response_model=MySupportMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_my_support_message(
    ticket_id: uuid.UUID,
    payload: SupportMessageCreateRequest,
    request: Request,
    blogger: BloggerViewer,
    _: None = Depends(require_csrf),
    limiter: RedisRateLimiter = Depends(get_rate_limiter),
    db: Session = Depends(get_db, scope="function"),
) -> MySupportMessageResponse:
    enforce_rate_limit(
        limiter,
        key=f"rate-limit:support:message:{blogger.id}",
        limit=60,
        window_seconds=3_600,
        fail_closed=False,
    )
    message = post_ticket_message(
        db,
        actor=blogger,
        ticket_id=ticket_id,
        payload=payload,
        is_staff=False,
        audit_context=context_from_request(request),
    )
    return my_message_response(message)


@router.get("/staff/support-tickets", response_model=StaffSupportTicketListResponse)
def get_staff_support_tickets(
    actor: SupportStaff,
    ticket_status: SupportStatus | None = Query(default=None, alias="status"),
    category: SupportCategory | None = None,
    blogger_id: uuid.UUID | None = Query(default=None, alias="bloggerId"),
    assignee_user_id: uuid.UUID | None = Query(default=None, alias="assigneeUserId"),
    unassigned_only: bool = Query(default=False, alias="unassignedOnly"),
    search: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> StaffSupportTicketListResponse:
    return list_staff_tickets(
        db,
        actor=actor,
        status=ticket_status,
        category=category,
        blogger_id=blogger_id,
        assignee_user_id=assignee_user_id,
        unassigned_only=unassigned_only,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.get("/staff/support-tickets/{ticket_id}", response_model=StaffSupportTicketDetailResponse)
def get_staff_support_ticket(
    ticket_id: uuid.UUID,
    actor: SupportStaff,
    db: Session = Depends(get_db, scope="function"),
) -> StaffSupportTicketDetailResponse:
    return get_staff_ticket(db, actor=actor, ticket_id=ticket_id)


@router.post(
    "/staff/support-tickets/{ticket_id}/messages",
    response_model=StaffSupportMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_staff_support_message(
    ticket_id: uuid.UUID,
    payload: SupportMessageCreateRequest,
    request: Request,
    actor: SupportStaff,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> StaffSupportMessageResponse:
    message = post_ticket_message(
        db,
        actor=actor,
        ticket_id=ticket_id,
        payload=payload,
        is_staff=True,
        audit_context=context_from_request(request),
    )
    return staff_message_response(message)


@router.post(
    "/staff/support-tickets/{ticket_id}/assignments",
    response_model=StaffSupportEventResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_support_assignment(
    ticket_id: uuid.UUID,
    payload: SupportAssignmentRequest,
    request: Request,
    actor: SupportStaff,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> StaffSupportEventResponse:
    event = assign_ticket(
        db,
        actor=actor,
        ticket_id=ticket_id,
        payload=payload,
        audit_context=context_from_request(request),
    )
    return staff_event_response(event)


@router.post(
    "/staff/support-tickets/{ticket_id}/status-transitions",
    response_model=StaffSupportEventResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_support_status_transition(
    ticket_id: uuid.UUID,
    payload: SupportStatusTransitionRequest,
    request: Request,
    actor: SupportStaff,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> StaffSupportEventResponse:
    event = transition_ticket_status(
        db,
        actor=actor,
        ticket_id=ticket_id,
        payload=payload,
        audit_context=context_from_request(request),
    )
    return staff_event_response(event)


@router.post(
    "/staff/support-tickets/{ticket_id}/recovery-decisions",
    response_model=StaffSupportEventResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_recovery_decision(
    ticket_id: uuid.UUID,
    payload: RecoveryDecisionRequest,
    request: Request,
    actor: RecoveryReviewer,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> StaffSupportEventResponse:
    event = decide_account_recovery(
        db,
        actor=actor,
        ticket_id=ticket_id,
        payload=payload,
        audit_context=context_from_request(request),
    )
    return staff_event_response(event)
