import uuid
from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.catalog.models import Brand


def _clean_text(value: str) -> str:
    return " ".join(value.split())


def _normalize_hashtags(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = raw_value.strip()
        if not value:
            raise ValueError("Hashtags cannot be empty")
        if any(character.isspace() for character in value):
            raise ValueError("A hashtag cannot contain whitespace")
        if not value.startswith("#"):
            value = f"#{value}"
        value = value.casefold()
        if value == "#":
            raise ValueError("A hashtag must contain text")
        if len(value) > 100:
            raise ValueError("A hashtag cannot exceed 100 characters")
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    if not normalized:
        raise ValueError("At least one hashtag is required")
    return normalized


class MarketplaceLink(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    url: AnyHttpUrl

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str) -> str:
        value = _clean_text(value)
        if not value:
            raise ValueError("Link label cannot be blank")
        return value


class ProductCreateRequest(BaseModel):
    brand: Brand
    model_name: str = Field(min_length=1, max_length=255)
    publication_name: str = Field(min_length=1, max_length=500)
    sku: str = Field(min_length=1, max_length=128)
    required_hashtags: list[str] = Field(min_length=1, max_length=20)
    content_hint: str | None = Field(default=None, max_length=2000)
    marketplace_links: list[MarketplaceLink] = Field(default_factory=list, max_length=10)
    is_active: bool = True

    @field_validator("model_name", "publication_name", "sku")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        value = _clean_text(value)
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("content_hint")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("required_hashtags")
    @classmethod
    def normalize_hashtags(cls, values: list[str]) -> list[str]:
        return _normalize_hashtags(values)


class ProductUpdateRequest(BaseModel):
    brand: Brand | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=255)
    publication_name: str | None = Field(default=None, min_length=1, max_length=500)
    sku: str | None = Field(default=None, min_length=1, max_length=128)
    required_hashtags: list[str] | None = Field(default=None, min_length=1, max_length=20)
    content_hint: str | None = Field(default=None, max_length=2000)
    marketplace_links: list[MarketplaceLink] | None = Field(default=None, max_length=10)
    is_active: bool | None = None

    @field_validator("model_name", "publication_name", "sku")
    @classmethod
    def clean_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = _clean_text(value)
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @field_validator("content_hint")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("required_hashtags")
    @classmethod
    def normalize_hashtags(cls, values: list[str] | None) -> list[str] | None:
        return _normalize_hashtags(values) if values is not None else None

    @model_validator(mode="after")
    def validate_patch(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        non_nullable = {
            "brand",
            "model_name",
            "publication_name",
            "sku",
            "required_hashtags",
            "marketplace_links",
            "is_active",
        }
        if any(getattr(self, field) is None for field in self.model_fields_set & non_nullable):
            raise ValueError("Only content_hint can be set to null")
        return self


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    brand: Brand
    model_name: str
    publication_name: str
    sku: str
    required_hashtags: list[str]
    content_hint: str | None
    marketplace_links: list[MarketplaceLink]
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int
