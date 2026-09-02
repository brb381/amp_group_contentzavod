import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.catalog.models import Brand
from app.creators.schemas import SocialAccountResponse
from app.content.models import (
    PublicationAvailability,
    PublicationEnrichmentStatus,
    PublicationParseStatus,
    PublicationStatus,
)
from app.platforms import Platform


def _clean_required(value: str) -> str:
    value = " ".join(value.split())
    if not value:
        raise ValueError("Value cannot be blank")
    return value


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class CatalogProductSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["catalog"]
    product_id: uuid.UUID


class UnlistedProductSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["unlisted"]
    brand: Brand
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _clean_required(value)


ProductSelection = Annotated[
    CatalogProductSelection | UnlistedProductSelection,
    Field(discriminator="type"),
]


class VideoCardCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    product: ProductSelection

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class VideoCardUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    product: ProductSelection | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        return _clean_required(value) if value is not None else None

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("Title cannot be null")
        if "product" in self.model_fields_set and self.product is None:
            raise ValueError("Product cannot be null")
        return self


class ProductSnapshot(BaseModel):
    brand: Brand
    model_name: str
    publication_name: str
    sku: str
    required_hashtags: list[str]


class ReportedProduct(BaseModel):
    brand: Brand
    name: str


class PublicationSummary(BaseModel):
    total: int = 0
    approved: int = 0
    pending_review: int = 0


class VideoCardResponse(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    product_id: uuid.UUID | None
    product_snapshot: ProductSnapshot | None
    reported_product: ReportedProduct | None
    is_product_resolved: bool
    status: Literal[
        "draft",
        "pending_review",
        "changes_required",
        "approved",
        "partially_approved",
        "rejected",
        "inactive",
    ]
    publication_summary: PublicationSummary = Field(default_factory=PublicationSummary)
    created_at: datetime
    updated_at: datetime


class VideoCardListResponse(BaseModel):
    items: list[VideoCardResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class PublicationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    social_account_id: uuid.UUID
    url: AnyHttpUrl


class PublicationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    social_account_id: uuid.UUID | None = None
    url: AnyHttpUrl | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Publication fields cannot be null")
        return self


class PublicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    video_card_id: uuid.UUID
    social_account_id: uuid.UUID
    platform: Platform
    submitted_url: str
    normalized_url: str
    external_id: str | None
    status: PublicationStatus
    parse_status: PublicationParseStatus
    availability: PublicationAvailability
    enrichment_status: PublicationEnrichmentStatus
    external_title: str | None
    external_author_id: str | None
    external_author_name: str | None
    external_published_at: datetime | None
    external_duration_seconds: int | None
    external_thumbnail_url: str | None
    enriched_at: datetime | None
    enrichment_error_code: str | None
    moderation_reason: str | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicationHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID
    event_type: str
    from_status: str | None
    to_status: str
    reason: str | None
    changes: dict
    created_at: datetime


class PublicationDetailResponse(PublicationResponse):
    history: list[PublicationHistoryResponse] = Field(default_factory=list)


class PublicationListResponse(BaseModel):
    items: list[PublicationResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class PublicationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "request_changes", "reject"]
    reason: str | None = Field(default=None, min_length=3, max_length=2000)
    resolved_product_id: uuid.UUID | None = None

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision in {"request_changes", "reject"} and not self.reason:
            raise ValueError("Reason is required for a negative decision")
        if self.decision != "approve" and self.resolved_product_id is not None:
            raise ValueError("resolved_product_id is available only for approval")
        return self


class PublicationModerationItem(BaseModel):
    publication: PublicationResponse
    card_title: str
    is_product_resolved: bool
    creator_user_id: uuid.UUID
    creator_email: str
    creator_full_name: str | None
    creator_display_name: str | None


class PublicationModerationDetail(BaseModel):
    publication: PublicationResponse
    card: VideoCardResponse
    social_account: SocialAccountResponse
    creator_user_id: uuid.UUID
    creator_email: str
    creator_full_name: str | None
    creator_display_name: str | None
    history: list[PublicationHistoryResponse] = Field(default_factory=list)


class PublicationModerationListResponse(BaseModel):
    items: list[PublicationModerationItem]
    page: int
    page_size: int
    total_items: int
    total_pages: int
