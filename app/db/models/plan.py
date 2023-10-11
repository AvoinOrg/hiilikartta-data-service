from datetime import datetime
from sqlalchemy import Column, String, DateTime, text
from sqlalchemy.dialects.postgresql import UUID, JSONB, ENUM
from sqlalchemy.orm import declarative_base, Mapped
from app.types.general import CalculationStatus
from uuid import uuid4

Base = declarative_base()


class Plan(Base):
    __tablename__ = "plan"

    id: Mapped[UUID] = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    ui_id: Mapped[str] = Column(String)
    user_id: Mapped[str] = Column(String)
    data: Mapped[dict] = Column(JSONB)
    created_ts: Mapped[datetime] = Column(
        DateTime, default=datetime.utcnow, server_default=text("current_timestamp(0)")
    )
    updated_ts: Mapped[datetime] = Column(
        DateTime,
        default=datetime.utcnow,
        server_default=text("current_timestamp(0)"),
        onupdate=datetime.utcnow,
    )
    calculated_ts: Mapped[datetime] = Column(DateTime)
    calculation_updated_ts: Mapped[datetime] = Column(DateTime)
    calculation_status = Column(
        ENUM(CalculationStatus, name="calculation_status_enum"), index=True
    )
    report_areas: Mapped[dict] = Column(JSONB)
    report_totals: Mapped[dict] = Column(JSONB)