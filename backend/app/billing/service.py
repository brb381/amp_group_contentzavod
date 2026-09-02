import math
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditAction, AuditContext, record_event
from app.auth.models import AccountStatus, Role, User
from app.billing.models import (
    AccrualCorrection,
    BalanceLedgerEntry,
    CalculationJob,
    CalculationPeriod,
    CalculationPeriodStatus,
    CreatorBalance,
    CreatorPeriodTotal,
    PublicationAccrual,
    RateVersion,
)
from app.billing.locking import lock_billing_control
from app.billing.policy import accrual_amount, next_month
from app.billing.schemas import (
    AccrualCorrectionRequest,
    CalculationPeriodListResponse,
    CreatorBalanceResponse,
    EarningsListItem,
    EarningsListResponse,
    EarningsPeriodResponse,
    PublicationAccrualListResponse,
    RateCreateRequest,
)
from app.billing.wallet import lock_creator_balance
from app.clock import utc_now
from app.database.locking import set_transaction_timeouts
from app.errors import APIError
from app.notifications.models import NotificationSeverity
from app.notifications.service import NotificationCommand, create_notification
from app.readings.models import ReadingStatus, ViewReading, YouTubeViewCollectionJob
from app.readings.revision import lock_reading_dataset_revision


CALCULATION_REVIEW_ROLES = {Role.MODERATOR, Role.ADMIN}
BILLING_LOCK_TIMEOUT_MS = 5_000
BILLING_STATEMENT_TIMEOUT_MS = 30_000


def _enum_value(value) -> str:
    return getattr(value, "value", value)


def _lock_actor(db: Session, actor: User, roles: set[Role], error_code: str) -> User:
    set_transaction_timeouts(
        db,
        lock_timeout_ms=BILLING_LOCK_TIMEOUT_MS,
        statement_timeout_ms=BILLING_STATEMENT_TIMEOUT_MS,
    )
    locked = db.scalar(
        select(User)
        .where(User.id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not locked or locked.status != AccountStatus.ACTIVE or locked.role not in roles:
        raise APIError(403, error_code, "Billing permissions changed")
    return locked


def _matches_integrity_error(
    error: IntegrityError,
    *,
    constraint_names: set[str],
    sqlite_markers: tuple[str, ...],
) -> bool:
    diagnostic = getattr(error.orig, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name is not None:
        return constraint_name in constraint_names
    message = str(error.orig).lower()
    return any(marker in message for marker in sqlite_markers)


def create_rate(
    db: Session,
    *,
    actor: User,
    payload: RateCreateRequest,
    audit_context: AuditContext,
) -> RateVersion:
    locked_actor = _lock_actor(db, actor, {Role.ADMIN}, "RATE_PERMISSION_CHANGED")
    lock_billing_control(db)
    if db.scalar(
        select(RateVersion.id).where(
            RateVersion.effective_from_period == payload.effective_from_period
        )
    ):
        raise APIError(409, "RATE_VERSION_EXISTS", "A rate already exists for this effective period")
    latest_created_period = db.scalar(
        select(func.max(CalculationPeriod.period)).where(
            CalculationPeriod.period >= payload.effective_from_period
        )
    )
    if latest_created_period:
        raise APIError(
            409,
            "RATE_PERIOD_ALREADY_CREATED",
            "A rate cannot start in an already created calculation period",
        )
    rate = RateVersion(
        rate_kopecks_per_view=payload.rate_kopecks_per_view,
        effective_from_period=payload.effective_from_period,
        created_by_user_id=locked_actor.id,
        reason=payload.reason,
    )
    db.add(rate)
    try:
        db.flush()
    except IntegrityError as error:
        if not _matches_integrity_error(
            error,
            constraint_names={"uq_rate_versions_effective_from_period"},
            sqlite_markers=(
                "unique constraint failed: rate_versions.effective_from_period",
            ),
        ):
            raise
        db.rollback()
        raise APIError(409, "RATE_VERSION_EXISTS", "A rate already exists for this effective period") from error
    record_event(
        db,
        context=audit_context,
        action=AuditAction.BILLING_RATE_CREATED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="rate_version",
        object_id=rate.id,
        metadata={
            "effective_from_period": rate.effective_from_period.isoformat(),
            "rate_kopecks_per_view": rate.rate_kopecks_per_view,
        },
    )
    return rate


def list_rates(db: Session) -> list[RateVersion]:
    return list(
        db.scalars(
            select(RateVersion).order_by(
                RateVersion.effective_from_period.desc(), RateVersion.created_at.desc()
            )
        )
    )


def list_calculation_periods(
    db: Session,
    *,
    status: CalculationPeriodStatus | None,
    page: int,
    page_size: int,
) -> CalculationPeriodListResponse:
    filters = [CalculationPeriod.status == status] if status else []
    total = db.scalar(select(func.count()).select_from(CalculationPeriod).where(*filters)) or 0
    items = list(
        db.scalars(
            select(CalculationPeriod)
            .where(*filters)
            .order_by(CalculationPeriod.period.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return CalculationPeriodListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )


def get_calculation_period(db: Session, period_id: uuid.UUID) -> CalculationPeriod:
    period = db.get(CalculationPeriod, period_id)
    if not period:
        raise APIError(404, "CALCULATION_PERIOD_NOT_FOUND", "Calculation period was not found")
    return period


def list_period_accruals(
    db: Session,
    *,
    period_id: uuid.UUID,
    blogger_id: uuid.UUID | None,
    suspicious_only: bool,
    page: int,
    page_size: int,
) -> PublicationAccrualListResponse:
    if not db.get(CalculationPeriod, period_id):
        raise APIError(404, "CALCULATION_PERIOD_NOT_FOUND", "Calculation period was not found")
    filters = [PublicationAccrual.period_id == period_id]
    if blogger_id:
        filters.append(PublicationAccrual.blogger_id == blogger_id)
    if suspicious_only:
        filters.append(PublicationAccrual.risk_flags != [])
    total = db.scalar(select(func.count()).select_from(PublicationAccrual).where(*filters)) or 0
    items = list(
        db.scalars(
            select(PublicationAccrual)
            .where(*filters)
            .order_by(PublicationAccrual.blogger_id, PublicationAccrual.publication_id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return PublicationAccrualListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )


def request_recalculation(
    db: Session,
    *,
    actor: User,
    period_id: uuid.UUID,
    audit_context: AuditContext,
) -> CalculationPeriod:
    locked_actor = _lock_actor(
        db, actor, CALCULATION_REVIEW_ROLES, "CALCULATION_PERMISSION_CHANGED"
    )
    period = db.scalar(
        select(CalculationPeriod).where(CalculationPeriod.id == period_id).with_for_update()
    )
    if not period:
        raise APIError(404, "CALCULATION_PERIOD_NOT_FOUND", "Calculation period was not found")
    if _enum_value(period.status) == "confirmed":
        raise APIError(409, "CALCULATION_PERIOD_CONFIRMED", "A confirmed period cannot be recalculated")
    job = db.scalar(
        select(CalculationJob).where(CalculationJob.period_id == period.id).with_for_update()
    )
    if not job:
        job = CalculationJob(period_id=period.id, state="pending", available_at=utc_now())
        db.add(job)
    elif job.state in {"queued", "processing"}:
        raise APIError(409, "CALCULATION_ALREADY_RUNNING", "Calculation is already running")
    else:
        job.state = "pending"
        job.available_at = utc_now()
        job.lease_until = None
        job.dispatch_id = None
        job.last_error_code = None
        job.attempt_count = 0
    period.status = "pending"
    record_event(
        db,
        context=audit_context,
        action=AuditAction.CALCULATION_RECALCULATION_REQUESTED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="calculation_period",
        object_id=period.id,
        metadata={"period": period.period.isoformat()},
    )
    return period


def confirm_period(
    db: Session,
    *,
    actor: User,
    period_id: uuid.UUID,
    audit_context: AuditContext,
) -> CalculationPeriod:
    locked_actor = _lock_actor(
        db, actor, CALCULATION_REVIEW_ROLES, "CALCULATION_PERMISSION_CHANGED"
    )
    revision = lock_reading_dataset_revision(db)
    period = db.scalar(
        select(CalculationPeriod).where(CalculationPeriod.id == period_id).with_for_update()
    )
    if not period:
        raise APIError(404, "CALCULATION_PERIOD_NOT_FOUND", "Calculation period was not found")
    if _enum_value(period.status) != "preliminary":
        raise APIError(409, "CALCULATION_NOT_PRELIMINARY", "Only a preliminary calculation can be confirmed")
    if period.input_revision is None or period.input_revision != revision.revision:
        raise APIError(409, "CALCULATION_STALE", "Readings changed after this calculation")
    earlier_open = db.scalar(
        select(CalculationPeriod.id)
        .where(
            CalculationPeriod.period < period.period,
            CalculationPeriod.status != "confirmed",
        )
        .limit(1)
    )
    if earlier_open:
        raise APIError(409, "EARLIER_PERIOD_NOT_CONFIRMED", "Earlier periods must be confirmed first")
    job = db.scalar(select(CalculationJob).where(CalculationJob.period_id == period.id))
    if not job or job.state != "succeeded":
        raise APIError(409, "CALCULATION_JOB_INCOMPLETE", "Calculation job has not completed")
    pending_reading = db.scalar(
        select(ViewReading.id)
        .where(
            ViewReading.reporting_period <= period.period,
            ViewReading.status == ReadingStatus.PENDING,
        )
        .limit(1)
    )
    if pending_reading:
        raise APIError(409, "READINGS_AWAIT_REVIEW", "Some readings still await review")
    period_end = next_month(period.period) - timedelta(days=1)
    active_youtube_job = db.scalar(
        select(YouTubeViewCollectionJob.id)
        .where(
            YouTubeViewCollectionJob.collection_date <= period_end,
            YouTubeViewCollectionJob.state.in_(
                ("pending", "queued", "processing", "retry_wait")
            ),
        )
        .limit(1)
    )
    if active_youtube_job:
        raise APIError(409, "YOUTUBE_COLLECTION_INCOMPLETE", "YouTube collection is still in progress")

    readings = list(
        db.scalars(
            select(ViewReading)
            .where(
                ViewReading.reporting_period <= period.period,
                ViewReading.financial_locked_at.is_(None),
            )
            .order_by(ViewReading.id)
            .with_for_update()
        )
    )
    totals = list(
        db.scalars(
            select(CreatorPeriodTotal)
            .where(CreatorPeriodTotal.period_id == period.id)
            .order_by(CreatorPeriodTotal.blogger_id)
        )
    )
    now = utc_now()
    for total in totals:
        ledger_key = f"period-accrual:{period.id}:{total.blogger_id}"
        if db.scalar(
            select(BalanceLedgerEntry.id).where(
                BalanceLedgerEntry.idempotency_key == ledger_key
            )
        ):
            raise APIError(409, "PERIOD_ALREADY_CREDITED", "Period money was already credited")
        balance = lock_creator_balance(db, blogger_id=total.blogger_id)
        balance.available_kopecks += total.amount_kopecks
        db.add(
            BalanceLedgerEntry(
                blogger_id=total.blogger_id,
                operation_type="period_accrual",
                available_delta_kopecks=total.amount_kopecks,
                reserved_delta_kopecks=0,
                paid_delta_kopecks=0,
                reference_type="calculation_period",
                reference_id=period.id,
                idempotency_key=ledger_key,
            )
        )
    for reading in readings:
        if reading.financial_locked_at is None:
            reading.financial_locked_at = now
            reading.financial_locked_by_period_id = period.id
    if revision.closed_through_period is None or revision.closed_through_period < period.period:
        revision.closed_through_period = period.period
    period.status = "confirmed"
    period.confirmed_at = now
    period.confirmed_by_user_id = locked_actor.id
    record_event(
        db,
        context=audit_context,
        action=AuditAction.CALCULATION_PERIOD_CONFIRMED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="calculation_period",
        object_id=period.id,
        metadata={
            "period": period.period.isoformat(),
            "amount_kopecks": period.total_amount_kopecks,
            "creator_count": len(totals),
        },
    )
    for total in totals:
        create_notification(
            db,
            NotificationCommand(
                recipient_user_id=total.blogger_id,
                template_code="calculation_confirmed",
                context={
                    "period": period.period.isoformat(),
                    "amount_kopecks": total.amount_kopecks,
                },
                severity=NotificationSeverity.INFO,
                deduplication_key=f"calculation:{period.id}:confirmed:{total.blogger_id}",
                related_object_type="calculation_period",
                related_object_id=period.id,
                action_path="/earnings",
            ),
        )
    return period


def list_my_earnings(
    db: Session,
    *,
    actor: User,
    page: int,
    page_size: int,
) -> EarningsListResponse:
    filters = [
        CreatorPeriodTotal.blogger_id == actor.id,
        CalculationPeriod.status.in_(("preliminary", "confirmed")),
    ]
    total = db.scalar(
        select(func.count())
        .select_from(CreatorPeriodTotal)
        .join(CalculationPeriod)
        .where(*filters)
    ) or 0
    rows = db.execute(
        select(CalculationPeriod, CreatorPeriodTotal)
        .join(CreatorPeriodTotal, CreatorPeriodTotal.period_id == CalculationPeriod.id)
        .where(*filters)
        .order_by(CalculationPeriod.period.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return EarningsListResponse(
        items=[EarningsListItem(period=period, total=item) for period, item in rows],
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=math.ceil(total / page_size),
    )


def get_my_earnings_period(
    db: Session,
    *,
    actor: User,
    period_value: date,
) -> EarningsPeriodResponse:
    row = db.execute(
        select(CalculationPeriod, CreatorPeriodTotal)
        .join(CreatorPeriodTotal, CreatorPeriodTotal.period_id == CalculationPeriod.id)
        .where(
            CalculationPeriod.period == period_value,
            CalculationPeriod.status.in_(("preliminary", "confirmed")),
            CreatorPeriodTotal.blogger_id == actor.id,
        )
    ).one_or_none()
    if not row:
        raise APIError(404, "EARNINGS_PERIOD_NOT_FOUND", "Earnings period was not found")
    period, total = row
    accruals = list(
        db.scalars(
            select(PublicationAccrual)
            .where(
                PublicationAccrual.period_id == period.id,
                PublicationAccrual.blogger_id == actor.id,
            )
            .order_by(PublicationAccrual.publication_id)
        )
    )
    return EarningsPeriodResponse(period=period, total=total, accruals=accruals)


def get_my_balance(db: Session, *, actor: User) -> CreatorBalanceResponse:
    balance = db.get(CreatorBalance, actor.id)
    if balance:
        return CreatorBalanceResponse.model_validate(balance)
    return CreatorBalanceResponse(
        blogger_id=actor.id,
        available_kopecks=0,
        reserved_kopecks=0,
        paid_kopecks=0,
        claim_expired_at=None,
        updated_at=None,
    )


def correct_confirmed_accrual(
    db: Session,
    *,
    actor: User,
    accrual_id: uuid.UUID,
    payload: AccrualCorrectionRequest,
    audit_context: AuditContext,
) -> AccrualCorrection:
    locked_actor = _lock_actor(db, actor, {Role.ADMIN}, "ACCRUAL_CORRECTION_PERMISSION_CHANGED")
    existing = db.scalar(
        select(AccrualCorrection).where(
            AccrualCorrection.idempotency_key == str(payload.idempotency_key)
        )
    )
    if existing:
        if (
            existing.accrual_id != accrual_id
            or existing.new_current_value != payload.corrected_current_value
            or existing.reason != payload.reason
        ):
            raise APIError(
                409,
                "IDEMPOTENCY_KEY_REUSED",
                "Idempotency key was already used with different correction data",
            )
        return existing
    accrual = db.scalar(
        select(PublicationAccrual)
        .where(PublicationAccrual.id == accrual_id)
        .with_for_update()
    )
    if not accrual:
        raise APIError(404, "ACCRUAL_NOT_FOUND", "Accrual was not found")
    period = db.scalar(
        select(CalculationPeriod)
        .where(CalculationPeriod.id == accrual.period_id)
        .with_for_update()
    )
    if not period or _enum_value(period.status) != "confirmed":
        raise APIError(409, "ACCRUAL_PERIOD_NOT_CONFIRMED", "Only confirmed accruals are corrected here")
    previous_correction = db.scalar(
        select(AccrualCorrection)
        .where(AccrualCorrection.accrual_id == accrual.id)
        .order_by(AccrualCorrection.sequence_number.desc())
        .limit(1)
    )
    old_current = (
        previous_correction.new_current_value if previous_correction else accrual.current_value
    )
    old_amount = accrual.payable_amount_kopecks
    _, new_amount, _ = accrual_amount(
        accrual.previous_value,
        payload.corrected_current_value,
        accrual.rate_kopecks_per_view,
    )
    correction = AccrualCorrection(
        accrual_id=accrual.id,
        sequence_number=(previous_correction.sequence_number + 1 if previous_correction else 1),
        old_current_value=old_current,
        new_current_value=payload.corrected_current_value,
        old_amount_kopecks=old_amount,
        new_amount_kopecks=new_amount,
        delta_kopecks=new_amount - old_amount,
        reason=payload.reason,
        actor_user_id=locked_actor.id,
        idempotency_key=str(payload.idempotency_key),
    )
    db.add(correction)
    try:
        db.flush()
    except IntegrityError as error:
        is_idempotency_conflict = _matches_integrity_error(
            error,
            constraint_names={"uq_accrual_corrections_idempotency_key"},
            sqlite_markers=(
                "unique constraint failed: accrual_corrections.idempotency_key",
            ),
        )
        is_sequence_conflict = _matches_integrity_error(
            error,
            constraint_names={"uq_accrual_correction_sequence"},
            sqlite_markers=(
                "unique constraint failed: accrual_corrections.accrual_id, "
                "accrual_corrections.sequence_number",
            ),
        )
        if not is_idempotency_conflict and not is_sequence_conflict:
            raise
        db.rollback()
        if is_sequence_conflict:
            raise APIError(
                409,
                "ACCRUAL_CONCURRENT_CHANGE",
                "The accrual changed concurrently; reload it and retry",
            ) from error
        existing = db.scalar(
            select(AccrualCorrection).where(
                AccrualCorrection.idempotency_key == str(payload.idempotency_key)
            )
        )
        if (
            existing
            and existing.accrual_id == accrual_id
            and existing.new_current_value == payload.corrected_current_value
            and existing.reason == payload.reason
        ):
            return existing
        raise APIError(
            409,
            "IDEMPOTENCY_KEY_REUSED",
            "Idempotency key was already used with different correction data",
        ) from error
    correction_delta = correction.delta_kopecks
    accrual.adjustment_kopecks += correction_delta
    creator_total = db.scalar(
        select(CreatorPeriodTotal)
        .where(
            CreatorPeriodTotal.period_id == period.id,
            CreatorPeriodTotal.blogger_id == accrual.blogger_id,
        )
        .with_for_update()
    )
    if not creator_total:
        raise APIError(409, "CREATOR_TOTAL_MISSING", "Creator period total is missing")
    creator_total.adjustment_kopecks += correction_delta
    period.total_adjustment_kopecks += correction_delta
    balance = lock_creator_balance(db, blogger_id=accrual.blogger_id)
    balance.available_kopecks += correction_delta
    db.add(
        BalanceLedgerEntry(
            blogger_id=accrual.blogger_id,
            operation_type="period_correction",
            available_delta_kopecks=correction_delta,
            reserved_delta_kopecks=0,
            paid_delta_kopecks=0,
            reference_type="accrual_correction",
            reference_id=correction.id,
            idempotency_key=f"accrual-correction:{payload.idempotency_key}",
        )
    )
    record_event(
        db,
        context=audit_context,
        action=AuditAction.ACCRUAL_CORRECTED,
        actor_user_id=locked_actor.id,
        actor_role=locked_actor.role.value,
        object_type="accrual_correction",
        object_id=correction.id,
        metadata={
            "accrual_id": str(accrual.id),
            "delta_kopecks": correction_delta,
        },
    )
    return correction
