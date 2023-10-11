from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete
from sqlalchemy.future import select
from app.db.models.plan import Plan
from typing import List, Optional
from uuid import UUID

# Note: I assume the AsyncSession is provided to each function via a parameter (e.g., from FastAPI dependency injection or similar)


async def get_plan_by_id(db_session: AsyncSession, calc_id: UUID) -> Optional[Plan]:
    result = await db_session.execute(select(Plan).filter_by(id=calc_id))
    return result.scalar_one_or_none()


async def get_all_plans(db_session: AsyncSession) -> List[Plan]:
    result = await db_session.execute(select(Plan))
    return result.scalars().all()


async def create_plan(db_session: AsyncSession, plan: Plan) -> Plan:
    db_session.add(plan)
    await db_session.commit()
    await db_session.refresh(plan)
    return plan


async def update_plan(db_session: AsyncSession, plan: Plan) -> bool:
    if not plan:
        return False

    await db_session.commit()
    return True


async def delete_plan(db_session: AsyncSession, calc_id: UUID) -> bool:
    plan = await get_plan_by_id(db_session, calc_id)
    if not plan:
        return False

    await db_session.execute(delete(Plan).filter_by(id=calc_id))
    await db_session.commit()
    return True
