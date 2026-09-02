import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, Index, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class Brand(str, enum.Enum):
    AMP = "AMP"
    AIRTONE = "AirTone"
    CRIOLIGHT = "CrioLight"


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("brand", "normalized_name", name="uq_products_brand_normalized_name"),
        UniqueConstraint("brand", "normalized_sku", name="uq_products_brand_normalized_sku"),
        Index("ix_products_active_brand_name", "is_active", "brand", "normalized_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    brand: Mapped[Brand] = mapped_column(
        Enum(Brand, values_callable=enum_values, name="productbrand"), nullable=False
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    publication_name: Mapped[str] = mapped_column(String(500), nullable=False)
    sku: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_sku: Mapped[str] = mapped_column(String(128), nullable=False)
    required_hashtags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    content_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    marketplace_links: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
