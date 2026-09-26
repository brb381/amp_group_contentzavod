from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field


class VKOEmbedResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = Field(max_length=500)
    author_name: str = Field(max_length=500)
    thumbnail_url: AnyHttpUrl
    provider_name: str
    html: str = Field(min_length=1)


class VKPublicVideo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(ge=1)
    owner_id: int
    views: int = Field(ge=0)

    @property
    def external_id(self) -> str:
        return f"{self.owner_id}_{self.id}"
