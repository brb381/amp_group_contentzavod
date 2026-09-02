import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from app.account_deletion.models import DeletionRequestStatus


class AccountDeletionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    password: str = Field(min_length=1, max_length=256)


class AccountDeletionConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    token: str = Field(min_length=32, max_length=512)


class AccountDeletionCancellationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID


class AccountDeletionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: DeletionRequestStatus
    requested_at: datetime
    expires_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None

    @field_serializer(
        "requested_at", "expires_at", "completed_at", "cancelled_at", when_used="json"
    )
    def serialize_utc(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class AccountDeletionListResponse(BaseModel):
    items: list[AccountDeletionResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int
