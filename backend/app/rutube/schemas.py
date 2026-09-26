from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class RutubeAuthor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int | str | None = None
    name: str | None = Field(default=None, max_length=500)


class RutubeVideo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    thumbnail_url: AnyHttpUrl
    duration: int | None = Field(default=None, ge=0)
    created_ts: datetime | None = None
    author: RutubeAuthor | None = None
    hits: int = Field(ge=0)