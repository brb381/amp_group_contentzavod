import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.readings.models import ReadingSource, ReadingStatus
from app.readings.limits import MAX_VIEW_COUNT


class ReadingValueRequest(BaseModel):
    value: int = Field(ge=0, le=MAX_VIEW_COUNT)


class ReadingReviewRequest(BaseModel):
    decision: Literal["accept", "reject", "correct"]
    accepted_value: int | None = Field(default=None, ge=0, le=MAX_VIEW_COUNT)
    reason: str | None = Field(default=None, min_length=3, max_length=1000)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision == "correct" and (self.accepted_value is None or not self.reason):
            raise ValueError("Correction requires accepted_value and reason")
        if self.decision == "reject" and not self.reason:
            raise ValueError("Rejection requires reason")
        if self.decision != "correct" and self.accepted_value is not None:
            raise ValueError("accepted_value is only allowed for correction")
        return self


class ReadingCorrectionRequest(BaseModel):
    accepted_value: int = Field(ge=0, le=MAX_VIEW_COUNT)
    reason: str = Field(min_length=3, max_length=1000)


class ViewReadingHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    action: str
    old_value: int | None
    new_value: int | None
    reason: str | None
    actor_user_id: uuid.UUID | None
    created_at: datetime


class ViewReadingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    publication_id: uuid.UUID
    reporting_period: date
    source: ReadingSource
    reported_value: int
    accepted_value: int | None
    status: ReadingStatus
    risk_flags: list[str]
    captured_at: datetime
    submitted_by_user_id: uuid.UUID | None
    reviewed_by_user_id: uuid.UUID | None
    reviewed_at: datetime | None
    review_reason: str | None
    created_at: datetime
    updated_at: datetime


class ViewReadingDetailResponse(ViewReadingResponse):
    history: list[ViewReadingHistoryResponse]


class ViewReadingListResponse(BaseModel):
    items: list[ViewReadingResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int
