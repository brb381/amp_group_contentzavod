import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.support.models import (
    SupportCategory,
    SupportEventType,
    SupportStatus,
)


class _Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupportTicketCreateRequest(_Command):
    idempotency_key: uuid.UUID
    category: SupportCategory
    subject: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)
    related_object_type: str | None = Field(default=None, max_length=64)
    related_object_id: uuid.UUID | None = None

    @field_validator("subject", "body")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("subject")
    @classmethod
    def reject_subject_line_breaks(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("subject must be a single line")
        return value

    @field_validator("related_object_type")
    @classmethod
    def validate_related_object_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or not value.replace("_", "").isalnum():
            raise ValueError("related_object_type must be an identifier")
        return value

    @model_validator(mode="after")
    def validate_related_object_pair(self):
        if (self.related_object_type is None) != (self.related_object_id is None):
            raise ValueError("related_object_type and related_object_id must be provided together")
        return self


class SupportMessageCreateRequest(_Command):
    idempotency_key: uuid.UUID
    body: str = Field(min_length=1, max_length=10_000)

    @field_validator("body")
    @classmethod
    def strip_required_body(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("body must not be blank")
        return value


class SupportAssignmentRequest(_Command):
    idempotency_key: uuid.UUID
    assignee_user_id: uuid.UUID | None
    reason: str | None = Field(default=None, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None


class SupportStatusTransitionRequest(_Command):
    idempotency_key: uuid.UUID
    to_status: SupportStatus
    reason: str | None = Field(default=None, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        return value or None


class RecoveryDecisionRequest(_Command):
    idempotency_key: uuid.UUID
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=2_000)

    @field_validator("reason")
    @classmethod
    def strip_required_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("reason must contain at least three characters")
        return value


class MySupportMessageResponse(BaseModel):
    id: uuid.UUID
    author_type: str
    body: str
    created_at: datetime


class StaffSupportMessageResponse(MySupportMessageResponse):
    author_user_id: uuid.UUID
    author_role: str


class MySupportEventResponse(BaseModel):
    id: uuid.UUID
    event_type: SupportEventType
    from_status: SupportStatus | None
    to_status: SupportStatus | None
    reason: str | None
    recovery_decision: str | None
    created_at: datetime


class StaffSupportEventResponse(MySupportEventResponse):
    actor_user_id: uuid.UUID
    previous_assignee_user_id: uuid.UUID | None
    new_assignee_user_id: uuid.UUID | None


class MySupportTicketResponse(BaseModel):
    id: uuid.UUID
    ticket_number: str
    category: SupportCategory
    subject: str
    status: SupportStatus
    related_object_type: str | None
    related_object_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None


class StaffSupportTicketResponse(MySupportTicketResponse):
    blogger_id: uuid.UUID
    assigned_to_user_id: uuid.UUID | None


class MySupportTicketDetailResponse(MySupportTicketResponse):
    messages: list[MySupportMessageResponse]
    history: list[MySupportEventResponse]


class StaffSupportTicketDetailResponse(StaffSupportTicketResponse):
    messages: list[StaffSupportMessageResponse]
    history: list[StaffSupportEventResponse]


class MySupportTicketListResponse(BaseModel):
    items: list[MySupportTicketResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class StaffSupportTicketListResponse(BaseModel):
    items: list[StaffSupportTicketResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int
