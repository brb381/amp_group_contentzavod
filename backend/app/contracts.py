import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


EMAIL_QUEUE = "email"
EMAIL_TASK = "email.deliver"
YOUTUBE_QUEUE = "youtube"
YOUTUBE_TASK = "youtube.enrich_publications"
TIKTOK_QUEUE = "tiktok"
TIKTOK_TASK = "tiktok.enrich_publication"
TIKTOK_VIEWS_TASK = "tiktok.collect_views"
VK_QUEUE = "vk"
VK_TASK = "vk.enrich_publication"
VK_VIEWS_TASK = "vk.collect_views"
RUTUBE_QUEUE = "rutube"
RUTUBE_TASK = "rutube.enrich_publication"
RUTUBE_VIEWS_TASK = "rutube.collect_views"
INSTAGRAM_QUEUE = "instagram"
INSTAGRAM_TASK = "instagram.enrich_publication"
INSTAGRAM_VIEWS_TASK = "instagram.collect_views"
DZEN_QUEUE = "dzen"
DZEN_TASK = "dzen.enrich_publication"
DZEN_VIEWS_TASK = "dzen.collect_views"
CALCULATIONS_QUEUE = "calculations"
CALCULATION_TASK = "calculations.build_period"
EXPORTS_QUEUE = "exports"
EXPORT_TASK = "exports.build"
LIFECYCLE_QUEUE = "lifecycle"
LIFECYCLE_TASK = "lifecycle.execute"


class EmailDeliveryCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    dispatch_id: uuid.UUID
    recipient: EmailStr
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("subject")
    @classmethod
    def subject_must_be_one_line(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("Email subject must be one line")
        return value


class YouTubeCommandItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    publication_id: uuid.UUID
    video_id: str = Field(min_length=1, max_length=255)


class YouTubeEnrichmentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_id: uuid.UUID
    items: list[YouTubeCommandItem] = Field(min_length=1, max_length=50)


class TikTokEnrichmentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_id: uuid.UUID
    job_id: uuid.UUID
    publication_id: uuid.UUID
    source_url: str = Field(min_length=1, max_length=2048)


class TikTokViewCollectionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_id: uuid.UUID
    job_id: uuid.UUID
    publication_id: uuid.UUID
    source_url: str = Field(min_length=1, max_length=2048)


class VKEnrichmentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_id: uuid.UUID
    job_id: uuid.UUID
    publication_id: uuid.UUID
    source_url: str = Field(min_length=1, max_length=2048)


class VKViewCollectionCommand(VKEnrichmentCommand):
    pass


class RutubeEnrichmentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_id: uuid.UUID
    job_id: uuid.UUID
    publication_id: uuid.UUID
    source_url: str = Field(min_length=1, max_length=2048)
    external_id: str = Field(min_length=1, max_length=255)


class RutubeViewCollectionCommand(RutubeEnrichmentCommand):
    pass


class InstagramEnrichmentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_id: uuid.UUID
    job_id: uuid.UUID
    publication_id: uuid.UUID
    source_url: str = Field(min_length=1, max_length=2048)


class InstagramViewCollectionCommand(InstagramEnrichmentCommand):
    pass


class DzenEnrichmentCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_id: uuid.UUID
    job_id: uuid.UUID
    publication_id: uuid.UUID
    source_url: str = Field(min_length=1, max_length=2048)


class DzenViewCollectionCommand(DzenEnrichmentCommand):
    pass


YOUTUBE_VIEWS_TASK = "youtube.collect_views"


class YouTubeViewCommandItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    publication_id: uuid.UUID
    video_id: str = Field(min_length=1, max_length=255)


class YouTubeViewCollectionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispatch_id: uuid.UUID
    items: list[YouTubeViewCommandItem] = Field(min_length=1, max_length=50)


class CalculationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_id: uuid.UUID
    dispatch_id: uuid.UUID


class ExportCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_id: uuid.UUID
    dispatch_id: uuid.UUID


class LifecycleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    dispatch_id: uuid.UUID
