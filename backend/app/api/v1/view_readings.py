import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import CurrentUser, require_active_roles, require_csrf
from app.auth.models import Role, User
from app.config import Settings, get_settings
from app.database.session import get_db
from app.readings.models import ReadingStatus
from app.readings.schemas import (
    ReadingCorrectionRequest,
    ReadingReviewRequest,
    ReadingValueRequest,
    ViewReadingDetailResponse,
    ViewReadingListResponse,
    ViewReadingResponse,
)
from app.readings.service import (
    correct_accepted_reading,
    create_manual_reading,
    list_my_readings,
    list_readings_for_review,
    reading_detail,
    review_reading,
    update_manual_reading,
)


router = APIRouter(tags=["view readings"])
Reviewer = Annotated[
    User,
    Depends(require_active_roles(Role.MODERATOR, Role.MANAGER, Role.ADMIN)),
]
Manager = Annotated[User, Depends(require_active_roles(Role.MANAGER, Role.ADMIN))]


@router.post(
    "/me/publications/{publication_id}/view-readings",
    response_model=ViewReadingDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_manual_reading(
    publication_id: uuid.UUID,
    payload: ReadingValueRequest,
    request: Request,
    user: CurrentUser,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> ViewReadingDetailResponse:
    reading = create_manual_reading(
        db,
        actor=user,
        publication_id=publication_id,
        value=payload.value,
        suspicious_growth_threshold=settings.suspicious_monthly_view_growth,
        audit_context=context_from_request(request),
    )
    return reading_detail(db, reading)


@router.patch(
    "/me/view-readings/{reading_id}",
    response_model=ViewReadingDetailResponse,
)
def patch_manual_reading(
    reading_id: uuid.UUID,
    payload: ReadingValueRequest,
    request: Request,
    user: CurrentUser,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
    settings: Settings = Depends(get_settings),
) -> ViewReadingDetailResponse:
    reading = update_manual_reading(
        db,
        actor=user,
        reading_id=reading_id,
        value=payload.value,
        suspicious_growth_threshold=settings.suspicious_monthly_view_growth,
        audit_context=context_from_request(request),
    )
    return reading_detail(db, reading)


@router.get("/me/view-readings", response_model=ViewReadingListResponse)
def get_my_view_readings(
    user: CurrentUser,
    publication_id: uuid.UUID | None = Query(default=None, alias="publicationId"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> ViewReadingListResponse:
    return list_my_readings(
        db,
        actor=user,
        publication_id=publication_id,
        page=page,
        page_size=page_size,
    )


@router.get("/moderation/view-readings", response_model=ViewReadingListResponse)
def get_view_reading_queue(
    _: Reviewer,
    reading_status: ReadingStatus | None = Query(
        default=ReadingStatus.PENDING, alias="status"
    ),
    period: date | None = Query(default=None),
    suspicious_only: bool = Query(default=False, alias="isSuspicious"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> ViewReadingListResponse:
    return list_readings_for_review(
        db,
        status=reading_status,
        period=period,
        suspicious_only=suspicious_only,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/moderation/view-readings/{reading_id}/decisions",
    response_model=ViewReadingResponse,
)
def post_view_reading_decision(
    reading_id: uuid.UUID,
    payload: ReadingReviewRequest,
    request: Request,
    reviewer: Reviewer,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> ViewReadingResponse:
    return review_reading(
        db,
        reviewer=reviewer,
        reading_id=reading_id,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.post(
    "/moderation/view-readings/{reading_id}/corrections",
    response_model=ViewReadingResponse,
)
def post_view_reading_correction(
    reading_id: uuid.UUID,
    payload: ReadingCorrectionRequest,
    request: Request,
    manager: Manager,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> ViewReadingResponse:
    return correct_accepted_reading(
        db,
        manager=manager,
        reading_id=reading_id,
        payload=payload,
        audit_context=context_from_request(request),
    )
