from sqlalchemy import Column, String, DateTime, Enum, JSON, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
import uuid
import json
from app.types.general import CalculationStatus
from app.utils.logger import get_logger
from datetime import datetime
from sqlalchemy.future import select
from app.utils.logger import get_logger

logger = get_logger(__name__)

Base = declarative_base()


class Plan(Base):
    __tablename__ = "plan"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ui_id = Column(String)
    user_id = Column(String)
    data = Column(JSON)
    created_ts = Column(DateTime, default=datetime.utcnow)
    updated_ts = Column(DateTime, default=datetime.utcnow)
    calculated_ts = Column(DateTime)
    calculation_status = Column(
        Enum(*[status.value for status in CalculationStatus]), nullable=False
    )
    report_areas = Column(JSON)
    report_totals = Column(JSON)


async def update_plan_status(db_session, calc_id, state, data=None):
    plan = await db_session.execute(select(Plan).filter_by(id=calc_id))
    plan = plan.scalar_one_or_none()

    if not plan:
        logger.error(f"Calculation ID {calc_id} not found in the database!")
        return False

    plan.calculation_status = state
    if data:
        plan.data = json.dumps(data)

    if state == CalculationStatus.FINISHED.value:
        plan.updated_ts = datetime.utcnow()
        plan.calculated_ts = datetime.utcnow()

    await db_session.commit()
    return True


async def get_plan_by_id(db_session, calc_id):
    plan = await db_session.execute(select(Plan).filter_by(id=calc_id))
    return plan.scalar_one_or_none()


async def create_plan(db_session, id, state):
    new_plan = Plan(id=id, calculation_status=state)
    db_session.add(new_plan)
    await db_session.commit()
