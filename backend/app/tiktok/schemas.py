from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class TikTokOEmbedResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(max_length=500)
    author_name: str = Field(max_length=500)
    author_url: AnyHttpUrl
    thumbnail_url: AnyHttpUrl
    provider_name: str


class TikTokPublicStats(BaseModel):
    model_config = ConfigDict(extra="ignore")

    playCount: int = Field(ge=0)


class TikTokPublicVideo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, max_length=255)
    stats: TikTokPublicStats
