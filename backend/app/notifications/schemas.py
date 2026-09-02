import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.notifications.models import (
    NotificationChannel,
    NotificationSeverity,
)


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    template_code: str
    severity: NotificationSeverity
    title: str
    body: str
    related_object_type: str | None
    related_object_id: uuid.UUID | None
    action_path: str | None
    read_at: datetime | None
    created_at: datetime
    expires_at: datetime | None


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class NotificationUnreadCountResponse(BaseModel):
    unread_count: int


class NotificationReadAllResponse(BaseModel):
    updated_count: int


class NotificationTemplateVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_template: str | None = Field(default=None, min_length=1, max_length=255)
    subject_template: str | None = Field(default=None, min_length=1, max_length=998)
    body_template: str = Field(min_length=1, max_length=10_000)
    allowed_variables: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("allowed_variables")
    @classmethod
    def validate_variables(cls, values: list[str]) -> list[str]:
        normalized = sorted(set(values))
        if len(normalized) != len(values):
            raise ValueError("allowed_variables must be unique")
        for value in normalized:
            if not value.isidentifier() or len(value) > 64:
                raise ValueError("allowed_variables contains an invalid identifier")
        return normalized


class NotificationTemplateVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    channel: NotificationChannel
    version: int
    title_template: str | None
    subject_template: str | None
    body_template: str
    allowed_variables: list[str]
    is_active: bool
    created_by_user_id: uuid.UUID | None
    created_at: datetime


class NotificationTemplateListResponse(BaseModel):
    items: list[NotificationTemplateVersionResponse]
