import enum
import uuid
from datetime import date, datetime
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from app.database.base import Base


def enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class CalculationPeriodStatus(str, enum.Enum):
    PENDING = "pending"
    PRELIMINARY = "preliminary"
    CONFIRMED = "confirmed"


class CalculationJobState(str, enum.Enum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AccrualExclusionReason(str, enum.Enum):
    MISSING_CURRENT_READING = "missing_current_reading"
    BASELINE_ONLY = "baseline_only"


class LedgerOperationType(str, enum.Enum):
    PERIOD_ACCRUAL = "period_accrual"
    PERIOD_CORRECTION = "period_correction"
    PAYOUT_RESERVED = "payout_reserved"
    PAYOUT_RELEASED = "payout_released"
    PAYOUT_PAID = "payout_paid"


class BillingControl(Base):
    __tablename__ = "billing_control"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_billing_control_singleton"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class RateVersion(Base):
    __tablename__ = "rate_versions"
    __table_args__ = (
        CheckConstraint("rate_kopecks_per_view > 0", name="ck_rate_versions_rate_positive"),
        UniqueConstraint(
            "effective_from_period",
            name="uq_rate_versions_effective_from_period",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    rate_kopecks_per_view: Mapped[int] = mapped_column(BigInteger, nullable=False)
    effective_from_period: Mapped[date] = mapped_column(Date, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CalculationPeriod(Base):
    __tablename__ = "calculation_periods"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'preliminary', 'confirmed')",
            name="ck_calculation_periods_status",
        ),
        CheckConstraint("input_revision IS NULL OR input_revision >= 0", name="ck_calculation_periods_revision_nonnegative"),
        CheckConstraint("total_views >= 0", name="ck_calculation_periods_views_nonnegative"),
        CheckConstraint("total_amount_kopecks >= 0", name="ck_calculation_periods_amount_nonnegative"),
        CheckConstraint(
            "total_amount_kopecks + total_adjustment_kopecks >= 0",
            name="ck_calculation_periods_payable_nonnegative",
        ),
        Index("ix_calculation_periods_status_period", "status", "period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    period: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    status: Mapped[CalculationPeriodStatus] = mapped_column(
        Enum(
            CalculationPeriodStatus,
            values_callable=enum_values,
            native_enum=False,
            length=20,
        ),
        nullable=False,
        default=CalculationPeriodStatus.PENDING,
    )
    rate_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="RESTRICT"), nullable=False
    )
    input_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    total_views: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_amount_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_adjustment_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def total_payable_kopecks(self) -> int:
        return self.total_amount_kopecks + self.total_adjustment_kopecks


class CalculationJob(Base):
    __tablename__ = "calculation_jobs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'queued', 'processing', 'retry_wait', 'succeeded', 'failed')",
            name="ck_calculation_jobs_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_calculation_jobs_attempt_nonnegative"),
        Index("ix_calculation_jobs_due", "state", "available_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calculation_periods.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    state: Mapped[CalculationJobState] = mapped_column(
        Enum(CalculationJobState, values_callable=enum_values, native_enum=False, length=20),
        nullable=False,
        default=CalculationJobState.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, unique=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PublicationAccrual(Base):
    __tablename__ = "publication_accruals"
    __table_args__ = (
        UniqueConstraint("period_id", "publication_id", name="uq_publication_accrual_period_publication"),
        CheckConstraint("previous_value IS NULL OR previous_value >= 0", name="ck_publication_accrual_previous_nonnegative"),
        CheckConstraint("current_value IS NULL OR current_value >= 0", name="ck_publication_accrual_current_nonnegative"),
        CheckConstraint("eligible_views >= 0", name="ck_publication_accrual_views_nonnegative"),
        CheckConstraint("rate_kopecks_per_view > 0", name="ck_publication_accrual_rate_positive"),
        CheckConstraint("amount_kopecks >= 0", name="ck_publication_accrual_amount_nonnegative"),
        CheckConstraint(
            "amount_kopecks + adjustment_kopecks >= 0",
            name="ck_publication_accrual_payable_nonnegative",
        ),
        CheckConstraint(
            "exclusion_reason IS NULL OR exclusion_reason IN ('missing_current_reading', 'baseline_only')",
            name="ck_publication_accrual_exclusion_reason",
        ),
        Index("ix_publication_accruals_period_blogger", "period_id", "blogger_id"),
        Index("ix_publication_accruals_publication_period", "publication_id", "period_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calculation_periods.id", ondelete="RESTRICT"), nullable=False
    )
    publication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publications.id", ondelete="RESTRICT"), nullable=False
    )
    blogger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    previous_reading_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("view_readings.id", ondelete="RESTRICT"), nullable=True
    )
    previous_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    previous_reading_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_reading_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("view_readings.id", ondelete="RESTRICT"), nullable=True
    )
    current_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    current_reading_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    eligible_views: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rate_kopecks_per_view: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    adjustment_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    exclusion_reason: Mapped[AccrualExclusionReason | None] = mapped_column(
        Enum(
            AccrualExclusionReason,
            values_callable=enum_values,
            native_enum=False,
            length=32,
        ),
        nullable=True,
    )
    risk_flags: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def payable_amount_kopecks(self) -> int:
        return self.amount_kopecks + self.adjustment_kopecks


class CreatorPeriodTotal(Base):
    __tablename__ = "creator_period_totals"
    __table_args__ = (
        UniqueConstraint("period_id", "blogger_id", name="uq_creator_period_total_period_blogger"),
        CheckConstraint("eligible_views >= 0", name="ck_creator_period_total_views_nonnegative"),
        CheckConstraint("amount_kopecks >= 0", name="ck_creator_period_total_amount_nonnegative"),
        CheckConstraint(
            "amount_kopecks + adjustment_kopecks >= 0",
            name="ck_creator_period_total_payable_nonnegative",
        ),
        CheckConstraint("publication_count >= 0", name="ck_creator_period_total_publications_nonnegative"),
        CheckConstraint("risk_count >= 0", name="ck_creator_period_total_risks_nonnegative"),
        Index("ix_creator_period_totals_blogger_period", "blogger_id", "period_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    period_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calculation_periods.id", ondelete="RESTRICT"), nullable=False
    )
    blogger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    eligible_views: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    amount_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    adjustment_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    publication_count: Mapped[int] = mapped_column(nullable=False, default=0)
    risk_count: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def payable_amount_kopecks(self) -> int:
        return self.amount_kopecks + self.adjustment_kopecks


class CreatorBalance(Base):
    __tablename__ = "creator_balances"
    __table_args__ = (
        CheckConstraint("reserved_kopecks >= 0", name="ck_creator_balances_reserved_nonnegative"),
        CheckConstraint("paid_kopecks >= 0", name="ck_creator_balances_paid_nonnegative"),
    )

    blogger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    available_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    paid_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    claim_expired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BalanceLedgerEntry(Base):
    __tablename__ = "balance_ledger"
    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('period_accrual', 'period_correction', "
            "'payout_reserved', 'payout_released', 'payout_paid')",
            name="ck_balance_ledger_operation_type",
        ),
        CheckConstraint(
            "operation_type NOT IN ('payout_reserved', 'payout_released', 'payout_paid') OR "
            "(operation_type = 'payout_reserved' AND available_delta_kopecks < 0 AND "
            "reserved_delta_kopecks = -available_delta_kopecks AND paid_delta_kopecks = 0) OR "
            "(operation_type = 'payout_released' AND available_delta_kopecks > 0 AND "
            "reserved_delta_kopecks = -available_delta_kopecks AND paid_delta_kopecks = 0) OR "
            "(operation_type = 'payout_paid' AND available_delta_kopecks = 0 AND "
            "reserved_delta_kopecks < 0 AND "
            "paid_delta_kopecks = -reserved_delta_kopecks)",
            name="ck_balance_ledger_payout_transfer",
        ),
        Index("ix_balance_ledger_blogger_created", "blogger_id", "created_at"),
        Index("ix_balance_ledger_reference", "reference_type", "reference_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    blogger_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    operation_type: Mapped[LedgerOperationType] = mapped_column(
        Enum(LedgerOperationType, values_callable=enum_values, native_enum=False, length=32),
        nullable=False,
    )
    available_delta_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_delta_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    paid_delta_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reference_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AccrualCorrection(Base):
    __tablename__ = "accrual_corrections"
    __table_args__ = (
        CheckConstraint("old_current_value IS NULL OR old_current_value >= 0", name="ck_accrual_correction_old_value_nonnegative"),
        CheckConstraint("new_current_value >= 0", name="ck_accrual_correction_new_value_nonnegative"),
        CheckConstraint("old_amount_kopecks >= 0", name="ck_accrual_correction_old_amount_nonnegative"),
        CheckConstraint("new_amount_kopecks >= 0", name="ck_accrual_correction_new_amount_nonnegative"),
        CheckConstraint(
            "sequence_number >= 1", name="ck_accrual_correction_sequence_positive"
        ),
        Index("ix_accrual_corrections_accrual_created", "accrual_id", "created_at"),
        UniqueConstraint(
            "accrual_id", "sequence_number", name="uq_accrual_correction_sequence"
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_accrual_corrections_idempotency_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    accrual_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("publication_accruals.id", ondelete="RESTRICT"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(nullable=False)
    old_current_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    new_current_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    old_amount_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    new_amount_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    delta_kopecks: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
