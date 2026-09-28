from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ProgramSettings(Base):
    __tablename__ = "program_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_program_settings_singleton"),
        CheckConstraint(
            "suspicious_growth_threshold > 0",
            name="ck_program_settings_growth_positive",
        ),
        CheckConstraint(
            "random_review_percent BETWEEN 0 AND 100",
            name="ck_program_settings_review_percent",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    program_name: Mapped[str] = mapped_column(String(200), nullable=False, default="AMP Content Factory")
    main_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    primary_logo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    secondary_logo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    key_image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    manager_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    manager_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    manager_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    manager_telegram_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    program_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    suspicious_growth_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=500_000)
    random_review_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    rejection_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
