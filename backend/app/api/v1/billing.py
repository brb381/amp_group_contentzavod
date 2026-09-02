import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.audit.http import context_from_request
from app.auth.dependencies import CurrentUser, require_active_roles, require_csrf
from app.auth.models import Role, User
from app.billing.models import CalculationPeriodStatus
from app.billing.schemas import (
    AccrualCorrectionRequest,
    AccrualCorrectionResponse,
    CalculationPeriodListResponse,
    CalculationPeriodResponse,
    CreatorBalanceResponse,
    EarningsListResponse,
    EarningsPeriodResponse,
    PublicationAccrualListResponse,
    RateCreateRequest,
    RateResponse,
)
from app.billing.service import (
    confirm_period,
    correct_confirmed_accrual,
    create_rate,
    get_calculation_period,
    get_my_balance,
    get_my_earnings_period,
    list_calculation_periods,
    list_my_earnings,
    list_period_accruals,
    list_rates,
    request_recalculation,
)
from app.database.session import get_db


router = APIRouter(tags=["billing"])
BillingViewer = Annotated[
    User,
    Depends(
        require_active_roles(
            Role.MODERATOR,
            Role.MANAGER,
            Role.FINANCE,
            Role.ADMIN,
        )
    ),
]
CalculationReviewer = Annotated[
    User,
    Depends(require_active_roles(Role.MODERATOR, Role.ADMIN)),
]
Admin = Annotated[User, Depends(require_active_roles(Role.ADMIN))]


@router.get("/admin/billing/rates", response_model=list[RateResponse])
def get_rates(_: Admin, db: Session = Depends(get_db, scope="function")) -> list[RateResponse]:
    return list_rates(db)


@router.post(
    "/admin/billing/rates",
    response_model=RateResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_rate(
    payload: RateCreateRequest,
    request: Request,
    admin: Admin,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> RateResponse:
    return create_rate(
        db,
        actor=admin,
        payload=payload,
        audit_context=context_from_request(request),
    )


@router.get(
    "/moderation/calculation-periods",
    response_model=CalculationPeriodListResponse,
)
def get_periods(
    _: BillingViewer,
    calculation_status: CalculationPeriodStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> CalculationPeriodListResponse:
    return list_calculation_periods(
        db,
        status=calculation_status,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/moderation/calculation-periods/{period_id}",
    response_model=CalculationPeriodResponse,
)
def get_period(
    period_id: uuid.UUID,
    _: BillingViewer,
    db: Session = Depends(get_db, scope="function"),
) -> CalculationPeriodResponse:
    return get_calculation_period(db, period_id)


@router.get(
    "/moderation/calculation-periods/{period_id}/accruals",
    response_model=PublicationAccrualListResponse,
)
def get_period_accruals(
    period_id: uuid.UUID,
    _: BillingViewer,
    blogger_id: uuid.UUID | None = Query(default=None, alias="bloggerId"),
    suspicious_only: bool = Query(default=False, alias="isSuspicious"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> PublicationAccrualListResponse:
    return list_period_accruals(
        db,
        period_id=period_id,
        blogger_id=blogger_id,
        suspicious_only=suspicious_only,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/moderation/calculation-periods/{period_id}/recalculations",
    response_model=CalculationPeriodResponse,
)
def post_recalculation(
    period_id: uuid.UUID,
    request: Request,
    reviewer: CalculationReviewer,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> CalculationPeriodResponse:
    return request_recalculation(
        db,
        actor=reviewer,
        period_id=period_id,
        audit_context=context_from_request(request),
    )


@router.post(
    "/moderation/calculation-periods/{period_id}/confirmations",
    response_model=CalculationPeriodResponse,
)
def post_confirmation(
    period_id: uuid.UUID,
    request: Request,
    reviewer: CalculationReviewer,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> CalculationPeriodResponse:
    return confirm_period(
        db,
        actor=reviewer,
        period_id=period_id,
        audit_context=context_from_request(request),
    )


@router.get("/me/earnings", response_model=EarningsListResponse)
def get_earnings(
    user: CurrentUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    db: Session = Depends(get_db, scope="function"),
) -> EarningsListResponse:
    return list_my_earnings(db, actor=user, page=page, page_size=page_size)


@router.get("/me/earnings/{period}", response_model=EarningsPeriodResponse)
def get_earnings_period(
    period: date,
    user: CurrentUser,
    db: Session = Depends(get_db, scope="function"),
) -> EarningsPeriodResponse:
    return get_my_earnings_period(db, actor=user, period_value=period)


@router.get("/me/balance", response_model=CreatorBalanceResponse)
def get_balance(
    user: CurrentUser,
    db: Session = Depends(get_db, scope="function"),
) -> CreatorBalanceResponse:
    return get_my_balance(db, actor=user)


@router.post(
    "/admin/billing/accruals/{accrual_id}/corrections",
    response_model=AccrualCorrectionResponse,
)
def post_accrual_correction(
    accrual_id: uuid.UUID,
    payload: AccrualCorrectionRequest,
    request: Request,
    admin: Admin,
    _: None = Depends(require_csrf),
    db: Session = Depends(get_db, scope="function"),
) -> AccrualCorrectionResponse:
    return correct_confirmed_accrual(
        db,
        actor=admin,
        accrual_id=accrual_id,
        payload=payload,
        audit_context=context_from_request(request),
    )
