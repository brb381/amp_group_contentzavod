import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.auth.models import AccountStatus, Role


class AdminUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: Role
    status: AccountStatus
    status_before_block: AccountStatus | None
    status_reason: str | None
    status_changed_at: datetime | None
    email_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminUserListResponse(BaseModel):
    items: list[AdminUserResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class RoleChangeRequest(BaseModel):
    role: Role
    reason: str = Field(max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Reason must contain at least 3 characters")
        return value


class AccessChangeRequest(BaseModel):
    is_blocked: bool
    reason: str = Field(max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("Reason must contain at least 3 characters")
        return value
