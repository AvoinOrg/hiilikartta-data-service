from datetime import datetime
from sqlalchemy import String, DateTime, text, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB, ENUM, TIMESTAMP
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column
from uuid import uuid4
from typing import Any

from app.types.general import CalculationStatus
from app.db.models.base import Base


class Plan(Base):
    __tablename__ = "plan"
    __table_args__ = (
        UniqueConstraint(
            "ui_id", name="ui_id_unique"
        ),  # Adds a unique constraint to ui_id
    )

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    ui_id: Mapped[str] = mapped_column(UUID, index=True)
    name: Mapped[str] = mapped_column(String, nullable=True)
    user_id: Mapped[str] = mapped_column(String, nullable=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=True)
    created_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        default=func.now,
        server_default=text("current_timestamp(0)"),
    )
    updated_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        default=func.now,
        server_default=text("current_timestamp(0)"),
        onupdate=func.now,
    )
    saved_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        default=func.now,
        server_default=text("current_timestamp(0)"),
        nullable=True,
    )
    last_accessed_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        default=func.now,
        server_default=text("current_timestamp(0)"),
    )
    total_indices: Mapped[int] = mapped_column(Integer, nullable=True)
    last_index: Mapped[int] = mapped_column(Integer, nullable=True)
    last_area_calculation_status = mapped_column(
        ENUM(CalculationStatus, name="calculation_status_enum"),
        nullable=True,
    )
    last_area_calculation_retries = mapped_column(Integer, default=0, nullable=True)
    calculated_ts: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    calculation_updated_ts: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    calculation_status = mapped_column(
        ENUM(CalculationStatus, name="calculation_status_enum"),
        nullable=True,
    )
    report_areas: Mapped[dict] = mapped_column(
        JSONB,
        nullable=True,
    )
    report_totals: Mapped[dict] = mapped_column(
        JSONB,
        nullable=True,
    )
