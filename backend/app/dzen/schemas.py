from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class DzenPublicStats(BaseModel):
    model_config = ConfigDict(extra="ignore")

    playCount: int = Field(ge=0)


class DzenPublicVideo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    author_name: str = Field(min_length=1, max_length=500)
    author_url: AnyHttpUrl
    thumbnail_url: AnyHttpUrl
    stats: DzenPublicStats
