from datetime import datetime
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ProgramSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_name: str = Field(min_length=1, max_length=200)
    main_text: str | None = Field(default=None, max_length=10000)
    primary_logo_url: str | None = Field(default=None, max_length=2048)
    secondary_logo_url: str | None = Field(default=None, max_length=2048)
    key_image_url: str | None = Field(default=None, max_length=2048)
    manager_name: str | None = Field(default=None, max_length=200)
    manager_email: EmailStr | None = None
    manager_phone: str | None = Field(default=None, max_length=50)
    manager_telegram_url: str | None = Field(default=None, max_length=2048)
    program_details: str | None = Field(default=None, max_length=10000)
    service_signature: str | None = Field(default=None, max_length=5000)
    suspicious_growth_threshold: int = Field(ge=1, le=1_000_000_000)
    random_review_percent: int = Field(ge=0, le=100)
    rejection_reasons: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("primary_logo_url", "secondary_logo_url", "key_image_url", "manager_telegram_url")
    @classmethod
    def validate_urls(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if not value.startswith(("https://", "http://")):
            raise ValueError("URL must use http or https")
        return value

    @field_validator("rejection_reasons")
    @classmethod
    def clean_reasons(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(value.split()) for value in values if value.strip()]
        if len(cleaned) != len(set(item.casefold() for item in cleaned)):
            raise ValueError("Rejection reasons must be unique")
        if any(len(item) > 500 for item in cleaned):
            raise ValueError("Rejection reason is too long")
        return cleaned


class ProgramSettingsResponse(ProgramSettingsUpdate):
    model_config = ConfigDict(from_attributes=True)

    updated_by_user_id: uuid.UUID | None = None
    updated_at: datetime | None = None