from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.auth.models import AccountStatus, Role


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    program_terms_document_id: UUID
    program_terms_accepted: Literal[True]
    personal_data_consent_document_id: UUID
    personal_data_consent_granted: Literal[True]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class EmailVerificationRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmationRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    new_password: str = Field(min_length=12, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    role: Role
    status: AccountStatus
    email_verified_at: datetime | None


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict[str, str] = {}
    request_id: str
