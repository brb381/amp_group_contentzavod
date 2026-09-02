import uuid
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.exports.models import ExportFormat, ExportJobStatus, ExportType
from app.payouts.models import PayoutStatus, RecipientType


class PayoutExportFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_from: date
    requested_to: date
    status: PayoutStatus | None = None
    recipient_type: RecipientType | None = None
    blogger_id: uuid.UUID | None = None
    request_number: str | None = Field(
        default=None, pattern=r"^PAY-[0-9]{8}-[A-F0-9]{19}$"
    )
    approved_from: date | None = None
    approved_to: date | None = None

    @model_validator(mode="after")
    def validate_ranges(self):
        if self.requested_from > self.requested_to:
            raise ValueError("requested_from must not be later than requested_to")
        if self.requested_to - self.requested_from > timedelta(days=366):
            raise ValueError("requested date range must not exceed 366 days")
        if self.approved_from and self.approved_to and self.approved_from > self.approved_to:
            raise ValueError("approved_from must not be later than approved_to")
        return self


class ExportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    export_type: ExportType
    format: ExportFormat
    filters: PayoutExportFilters


class ExportJobResponse(BaseModel):
    id: uuid.UUID
    export_type: ExportType
    format: ExportFormat
    schema_version: int
    status: ExportJobStatus
    filters: PayoutExportFilters
    attempt_count: int
    row_count: int | None
    file_size: int | None
    content_sha256: str | None
    data_as_of: datetime | None
    expires_at: datetime | None
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    download_url: str | None


class ExportJobListResponse(BaseModel):
    items: list[ExportJobResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int
