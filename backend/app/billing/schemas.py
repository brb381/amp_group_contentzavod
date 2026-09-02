import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.billing.models import CalculationPeriodStatus
from app.readings.limits import MAX_VIEW_COUNT


class RateCreateRequest(BaseModel):
    rate_kopecks_per_view: int = Field(ge=1, le=1_000_000)
    effective_from_period: date
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("effective_from_period")
    @classmethod
    def period_must_start_on_first_day(cls, value: date) -> date:
        if value.day != 1:
            raise ValueError("effective_from_period must be the first day of a month")
        return value


class RateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rate_kopecks_per_view: int
    effective_from_period: date
    created_by_user_id: uuid.UUID | None
    reason: str | None
    created_at: datetime


class CalculationPeriodResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    period: date
    status: CalculationPeriodStatus
    rate_version_id: uuid.UUID
    input_revision: int | None
    calculated_at: datetime | None
    confirmed_at: datetime | None
    confirmed_by_user_id: uuid.UUID | None
    total_views: int
    total_amount_kopecks: int
    total_adjustment_kopecks: int
    total_payable_kopecks: int
    created_at: datetime
    updated_at: datetime


class CalculationPeriodListResponse(BaseModel):
    items: list[CalculationPeriodResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class PublicationAccrualResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    period_id: uuid.UUID
    publication_id: uuid.UUID
    blogger_id: uuid.UUID
    previous_reading_id: uuid.UUID | None
    previous_value: int | None
    current_reading_id: uuid.UUID | None
    current_value: int | None
    eligible_views: int
    rate_kopecks_per_view: int
    amount_kopecks: int
    adjustment_kopecks: int
    payable_amount_kopecks: int
    exclusion_reason: str | None
    risk_flags: list[str]
    created_at: datetime


class PublicationAccrualListResponse(BaseModel):
    items: list[PublicationAccrualResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class CreatorPeriodTotalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    period_id: uuid.UUID
    blogger_id: uuid.UUID
    eligible_views: int
    amount_kopecks: int
    adjustment_kopecks: int
    payable_amount_kopecks: int
    publication_count: int
    risk_count: int


class EarningsPeriodResponse(BaseModel):
    period: CalculationPeriodResponse
    total: CreatorPeriodTotalResponse
    accruals: list[PublicationAccrualResponse]


class EarningsListItem(BaseModel):
    period: CalculationPeriodResponse
    total: CreatorPeriodTotalResponse


class EarningsListResponse(BaseModel):
    items: list[EarningsListItem]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class CreatorBalanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    blogger_id: uuid.UUID
    available_kopecks: int
    reserved_kopecks: int
    paid_kopecks: int
    claim_expired_at: datetime | None
    updated_at: datetime | None


class AccrualCorrectionRequest(BaseModel):
    corrected_current_value: int = Field(ge=0, le=MAX_VIEW_COUNT)
    reason: str = Field(min_length=3, max_length=1000)
    idempotency_key: uuid.UUID


class AccrualCorrectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    accrual_id: uuid.UUID
    sequence_number: int
    old_current_value: int | None
    new_current_value: int
    old_amount_kopecks: int
    new_amount_kopecks: int
    delta_kopecks: int
    reason: str
    actor_user_id: uuid.UUID
    idempotency_key: uuid.UUID
    created_at: datetime
