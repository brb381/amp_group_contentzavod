from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

from app.creators.models import ProfileStatus, RecipientStatus, SocialAccountStatus
from app.platforms import Platform


class ProfileUpsertRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=3, max_length=255)
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, min_length=7, max_length=32, pattern=r"^\+?[0-9 ()-]+$")
    telegram: str | None = Field(default=None, min_length=2, max_length=128)
    city_country: str | None = Field(default=None, max_length=255)
    content_topics: str | None = Field(default=None, max_length=500)
    recipient_status: RecipientStatus | None = None


class SocialAccountCreateRequest(BaseModel):
    platform: Platform
    url: AnyHttpUrl
    follower_count: int | None = Field(default=None, ge=0, le=2_000_000_000)


class SocialAccountUpdateRequest(BaseModel):
    platform: Platform | None = None
    url: AnyHttpUrl | None = None
    follower_count: int | None = Field(default=None, ge=0, le=2_000_000_000)


class SocialAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    platform: Platform
    url: str
    follower_count: int | None
    status: SocialAccountStatus
    moderation_reason: str | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SocialAccountHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: UUID
    event_type: str
    from_status: str | None
    to_status: str
    reason: str | None
    created_at: datetime


class SocialAccountReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, min_length=3, max_length=2000)

    @model_validator(mode="after")
    def require_rejection_reason(self):
        if self.decision == "reject" and not self.reason:
            raise ValueError("reason is required for rejection")
        return self


class SocialAccountModerationItem(BaseModel):
    account: SocialAccountResponse
    creator_user_id: UUID
    profile_id: UUID | None
    creator_full_name: str | None
    creator_display_name: str | None


class SocialAccountModerationDetail(SocialAccountModerationItem):
    history: list[SocialAccountHistoryResponse] = Field(default_factory=list)


class SocialAccountModerationListResponse(BaseModel):
    items: list[SocialAccountModerationItem]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class ProfileHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_user_id: UUID
    event_type: str
    from_status: str | None
    to_status: str
    reason: str | None
    changes: dict
    created_at: datetime


class ProfileSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    full_name: str | None
    display_name: str | None
    phone: str | None
    telegram: str | None
    city_country: str | None
    content_topics: str | None
    recipient_status: RecipientStatus | None
    status: ProfileStatus
    moderation_reason: str | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProfileResponse(ProfileSummaryResponse):
    social_accounts: list[SocialAccountResponse] = Field(default_factory=list)
    history: list[ProfileHistoryResponse] = Field(default_factory=list)


class ProfileEnvelope(BaseModel):
    profile: ProfileResponse | None


class ModerationReviewRequest(BaseModel):
    decision: Literal["start_review", "approve", "reject", "suspend", "block"]
    reason: str | None = Field(default=None, min_length=3, max_length=2000)

    @model_validator(mode="after")
    def require_reason_for_negative_decision(self):
        if self.decision in {"reject", "suspend", "block"} and not self.reason:
            raise ValueError("reason is required for this decision")
        return self


class ProfileListResponse(BaseModel):
    items: list[ProfileSummaryResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int
