import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import require_active_roles, require_csrf, require_roles
from app.auth.models import Role, User
from app.database.session import get_db
from app.errors import APIError
from app.payouts.models import PayoutStatus, ReceiptStatus, RecipientType
from app.payouts.schemas import (
    MyPayoutRequestListResponse,
    MyPayoutRequestResponse,
    PayoutApprovalRequest,
    PayoutCommandResponse,
    PayoutCreateRequest,
    PayoutDetailsResponse,
    PayoutDetailsUpsertRequest,
    PayoutPaymentRequest,
    PayoutReceiptRequest,
    PayoutRejectionRequest,
    PayoutRequestDetailResponse,
    PayoutRequestListResponse,
    PayoutReviewRequest,
    my_payout_response,
    payout_detail_response,
)
from app.payouts.service import (
    approve_payout_request,
    create_my_payout_request,
    get_my_payout_details,
    get_my_payout_request,
    get_staff_payout_request,
    list_my_payout_requests,
    list_staff_payout_requests,
    record_payout_payment,
    record_payout_receipt,
    reject_payout_request,
    review_payout_request,
    upsert_my_payout_details,
)


def _set_private_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(tags=["payouts"], dependencies=[Depends(_set_private_no_store)])

BloggerViewer = Annotated[User, Depends(require_roles(Role.BLOGGER))]
ActiveBlogger = Annotated[User, Depends(require_active_roles(Role.BLOGGER))]
StaffViewer = Annotated[
    User,
    Depends(require_active_roles(Role.MANAGER, Role.FINANCE, Role.ADMIN)),
]
PayoutReviewer = Annotated[
    User,
    Depends(require_active_roles(Role.MANAGER, Role.ADMIN)),
]
PaymentRecorder = Annotated[
    User,
    Depends(require_active_roles(Role.FINANCE, Role.ADMIN)),
]
ReceiptRecorder = Annotated[
    User,
    Depends(require_active_roles(Role.MANAGER, Role.FINANCE, Role.ADMIN)),
]
PayoutRejector = Annotated[
    User,
    Depends(require_active_roles(Role.MANAGER, Role.FINANCE, Role.ADMIN)),
]


def _validate_requested_range(
    requested_from: date | None,
    requested_to: date | None,
) -> None:
    if requested_from and requested_to and requested_from > requested_to:
        raise APIError(
            422,
            "INVALID_DATE_RANGE",
            "requestedFrom must not be later than requestedTo",
        )


def _validate_approved_range(
    approved_from: date | None,
    approved_to: date | None,
) -> None:
    if approved_from and approved_to and approved_from > approved_to:
        raise APIError(
            422,
            "INVALID_DATE_RANGE",
            "approvedFrom must not be later than approvedTo",
        )


@router.get("/me/payout-details", response_model=PayoutDetailsResponse)
def get_payout_details(
    blogger: BloggerViewer,
    db: Session = Depends(get_db, scope="function"),
) -> PayoutDetailsResponse:
    return get_my_payout_details(db, actor=blogger)


@router.put("/me/payout-details", response_model=PayoutDetailsResponse)
def put_payout_details(
    payload: PayoutDetailsUpsertRequest,
    request: Request,
    blogger: ActiveBlogger,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PayoutDetailsResponse:
    return upsert_my_payout_details(
        db,
        actor=blogger,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.post(
    "/me/payout-requests",
    response_model=PayoutCommandResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_payout_request(
    payload: PayoutCreateRequest,
    request: Request,
    blogger: ActiveBlogger,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PayoutCommandResponse:
    return create_my_payout_request(
        db,
        actor=blogger,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.get("/me/payout-requests", response_model=MyPayoutRequestListResponse)
def get_my_payout_requests(
    blogger: BloggerViewer,
    payout_status: PayoutStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> MyPayoutRequestListResponse:
    return list_my_payout_requests(
        db,
        actor=blogger,
        status=payout_status,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/me/payout-requests/{payout_request_id}",
    response_model=MyPayoutRequestResponse,
)
def get_my_payout_request_detail(
    payout_request_id: uuid.UUID,
    blogger: BloggerViewer,
    db: Session = Depends(get_db, scope="function"),
) -> MyPayoutRequestResponse:
    return my_payout_response(
        get_my_payout_request(db, actor=blogger, payout_request_id=payout_request_id)
    )


@router.get("/staff/payout-requests", response_model=PayoutRequestListResponse)
def get_staff_payout_requests(
    staff: StaffViewer,
    payout_status: PayoutStatus | None = Query(default=None, alias="status"),
    recipient_type: RecipientType | None = Query(default=None, alias="recipientType"),
    blogger_id: uuid.UUID | None = Query(default=None, alias="bloggerId"),
    request_number: str | None = Query(
        default=None,
        alias="requestNumber",
        pattern=r"^PAY-[0-9]{8}-[A-F0-9]{19}$",
    ),
    requested_from: date | None = Query(default=None, alias="requestedFrom"),
    requested_to: date | None = Query(default=None, alias="requestedTo"),
    approved_from: date | None = Query(default=None, alias="approvedFrom"),
    approved_to: date | None = Query(default=None, alias="approvedTo"),
    due_before: date | None = Query(default=None, alias="dueBefore"),
    is_overdue: bool | None = Query(default=None, alias="isOverdue"),
    receipt_status: ReceiptStatus | None = Query(default=None, alias="receiptStatus"),
    receipt_due_before: date | None = Query(default=None, alias="receiptDueBefore"),
    is_receipt_overdue: bool | None = Query(
        default=None,
        alias="isReceiptOverdue",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> PayoutRequestListResponse:
    _validate_requested_range(requested_from, requested_to)
    _validate_approved_range(approved_from, approved_to)
    return list_staff_payout_requests(
        db,
        actor=staff,
        status=payout_status,
        recipient_type=recipient_type,
        blogger_id=blogger_id,
        request_number=request_number.strip() if request_number else None,
        requested_from=requested_from,
        requested_to=requested_to,
        approved_from=approved_from,
        approved_to=approved_to,
        due_before=due_before,
        is_overdue=is_overdue,
        receipt_status=receipt_status,
        receipt_due_before=receipt_due_before,
        is_receipt_overdue=is_receipt_overdue,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/staff/payout-requests/{payout_request_id}",
    response_model=PayoutRequestDetailResponse,
)
def get_staff_payout_request_detail(
    payout_request_id: uuid.UUID,
    staff: StaffViewer,
    db: Session = Depends(get_db, scope="function"),
) -> PayoutRequestDetailResponse:
    return payout_detail_response(
        get_staff_payout_request(
            db,
            actor=staff,
            payout_request_id=payout_request_id,
        )
    )


@router.post(
    "/staff/payout-requests/{payout_request_id}/reviews",
    response_model=PayoutCommandResponse,
)
def post_payout_review(
    payout_request_id: uuid.UUID,
    payload: PayoutReviewRequest,
    request: Request,
    reviewer: PayoutReviewer,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PayoutCommandResponse:
    return review_payout_request(
        db,
        actor=reviewer,
        payout_request_id=payout_request_id,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.post(
    "/staff/payout-requests/{payout_request_id}/approvals",
    response_model=PayoutCommandResponse,
)
def post_payout_approval(
    payout_request_id: uuid.UUID,
    payload: PayoutApprovalRequest,
    request: Request,
    reviewer: PayoutReviewer,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PayoutCommandResponse:
    return approve_payout_request(
        db,
        actor=reviewer,
        payout_request_id=payout_request_id,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.post(
    "/staff/payout-requests/{payout_request_id}/rejections",
    response_model=PayoutCommandResponse,
)
def post_payout_rejection(
    payout_request_id: uuid.UUID,
    payload: PayoutRejectionRequest,
    request: Request,
    reviewer: PayoutRejector,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PayoutCommandResponse:
    return reject_payout_request(
        db,
        actor=reviewer,
        payout_request_id=payout_request_id,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.post(
    "/staff/payout-requests/{payout_request_id}/payments",
    response_model=PayoutCommandResponse,
)
def post_payout_payment(
    payout_request_id: uuid.UUID,
    payload: PayoutPaymentRequest,
    request: Request,
    finance: PaymentRecorder,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PayoutCommandResponse:
    return record_payout_payment(
        db,
        actor=finance,
        payout_request_id=payout_request_id,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.post(
    "/staff/payout-requests/{payout_request_id}/receipts",
    response_model=PayoutCommandResponse,
)
def post_payout_receipt(
    payout_request_id: uuid.UUID,
    payload: PayoutReceiptRequest,
    request: Request,
    staff: ReceiptRecorder,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> PayoutCommandResponse:
    return record_payout_receipt(
        db,
        actor=staff,
        payout_request_id=payout_request_id,
        payload=payload,
        audit_context=context_from_request(request),
    )
