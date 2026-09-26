from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ExternalProviderState(Base):
    __tablename__ = "external_provider_states"
    __table_args__ = (
        CheckConstraint("status IN ('available', 'blocked')", name="ck_external_provider_state"),
    )

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="available")
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    block_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ExternalQuotaUsage(Base):
    __tablename__ = "external_quota_usage"

    provider: Mapped[str] = mapped_column(String(50), primary_key=True)
    quota_date: Mapped[date] = mapped_column(Date, primary_key=True)
    reserved_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    working_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
