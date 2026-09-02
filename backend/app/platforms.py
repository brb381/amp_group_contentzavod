import enum


class Platform(str, enum.Enum):
    YOUTUBE = "youtube"
    VK = "vk"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    DZEN = "dzen"
    RUTUBE = "rutube"


PLATFORM_VALUES = tuple(platform.value for platform in Platform)
PLATFORM_CHECK_SQL = "platform IN (" + ", ".join(f"'{value}'" for value in PLATFORM_VALUES) + ")"
