import uuid
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.catalog.models import Brand
from app.exports.models import ExportFormat, ExportJobStatus, ExportType
from app.platforms import Platform
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


class DataExportFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date
    date_to: date
    status: str | None = Field(default=None, min_length=1, max_length=64)
    blogger_id: uuid.UUID | None = None
    platform: Platform | None = None
    brand: Brand | None = None
    product_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_range(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from must not be later than date_to")
        if self.date_to - self.date_from > timedelta(days=366):
            raise ValueError("export date range must not exceed 366 days")
        return self


ExportFilters = PayoutExportFilters | DataExportFilters


class ExportCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    export_type: ExportType
    format: ExportFormat
    filters: ExportFilters

    @model_validator(mode="after")
    def validate_filter_type(self):
        payout_types = {ExportType.PAYOUT_REGISTER, ExportType.PAYOUT_HISTORY}
        if self.export_type in payout_types and not isinstance(
            self.filters, PayoutExportFilters
        ):
            raise ValueError("payout exports require payout filters")
        if self.export_type not in payout_types and not isinstance(
            self.filters, DataExportFilters
        ):
            raise ValueError("data exports require date filters")
        return self


class ExportJobResponse(BaseModel):
    id: uuid.UUID
    export_type: ExportType
    format: ExportFormat
    schema_version: int
    status: ExportJobStatus
    filters: ExportFilters
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
