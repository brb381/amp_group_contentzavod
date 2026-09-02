from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.legal.models import AcceptanceMethod, LegalDocumentType


class LegalDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_type: LegalDocumentType
    version: str
    revision: int
    title: str
    content_markdown: str
    content_sha256: str | None
    is_current: bool
    requires_reacceptance: bool
    change_summary: str | None
    published_at: datetime


class LegalDocumentSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_type: LegalDocumentType
    version: str
    revision: int
    title: str
    content_sha256: str | None
    is_current: bool
    requires_reacceptance: bool
    change_summary: str | None
    published_at: datetime


class CurrentLegalDocumentsResponse(BaseModel):
    program_terms: LegalDocumentResponse
    personal_data_consent: LegalDocumentResponse
    privacy_policy: LegalDocumentResponse


class LegalDocumentPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: LegalDocumentType
    version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    title: str = Field(min_length=3, max_length=255)
    content_markdown: str = Field(min_length=20, max_length=200_000)
    requires_reacceptance: bool = False
    change_summary: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_policy(self) -> "LegalDocumentPublishRequest":
        if self.document_type == LegalDocumentType.PRIVACY_POLICY and self.requires_reacceptance:
            raise ValueError("Privacy policy versions do not require acceptance")
        return self


class LegalDocumentListResponse(BaseModel):
    items: list[LegalDocumentSummaryResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class LegalAcceptanceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    accepted: Literal[True]


class LegalAcceptanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    method: AcceptanceMethod
    accepted_at: datetime
    withdrawn_at: datetime | None


class LegalAcceptanceHistoryItemResponse(LegalAcceptanceResponse):
    document_type: LegalDocumentType
    document_version: str
    document_title: str
    document_content_sha256: str | None


class LegalAcceptanceListResponse(BaseModel):
    items: list[LegalAcceptanceHistoryItemResponse]
    page: int
    page_size: int
    total_items: int
    total_pages: int


class RequiredLegalAcceptanceResponse(BaseModel):
    document_id: UUID
    document_type: LegalDocumentType
    version: str


class MyLegalStatusResponse(BaseModel):
    is_participation_allowed: bool
    required_acceptances: list[RequiredLegalAcceptanceResponse]
