from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.readings.limits import MAX_VIEW_COUNT


class YouTubeThumbnail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: AnyHttpUrl


class YouTubeSnippet(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(max_length=500)
    channelId: str = Field(max_length=255)
    channelTitle: str = Field(max_length=500)
    publishedAt: datetime
    thumbnails: dict[str, YouTubeThumbnail] = Field(default_factory=dict)


class YouTubeContentDetails(BaseModel):
    model_config = ConfigDict(extra="ignore")

    duration: str | None = Field(default=None, max_length=64)


class YouTubeVideo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(max_length=255)
    etag: str | None = Field(default=None, max_length=255)
    snippet: YouTubeSnippet
    contentDetails: YouTubeContentDetails | None = None


class YouTubeVideosResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[YouTubeVideo] = Field(max_length=50)


class YouTubeStatistics(BaseModel):
    model_config = ConfigDict(extra="ignore")

    viewCount: int = Field(ge=0, le=MAX_VIEW_COUNT)


class YouTubeViewCount(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(max_length=255)
    statistics: YouTubeStatistics


class YouTubeViewCountsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[YouTubeViewCount] = Field(max_length=50)
