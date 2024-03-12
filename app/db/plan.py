import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, text
from sqlalchemy.future import select
from app.db.models.plan import Plan
from typing import List, Optional, Any
from uuid import UUID
from sqlalchemy.orm import load_only

# Note: I assume the AsyncSession is provided to each function via a parameter (e.g., from FastAPI dependency injection or similar)


async def get_plan_by_id(db_session: AsyncSession, calc_id: UUID) -> Optional[Plan]:
    result = await db_session.execute(select(Plan).filter_by(id=calc_id))
    plan = result.scalars().first()

    return plan if plan else None


async def get_plan_without_data_by_ui_id(
    db_session: AsyncSession, ui_id: UUID
) -> Optional[Plan]:
    cols = [
        Plan.id,
        Plan.ui_id,
        Plan.name,
        Plan.user_id,
        Plan.created_ts,
        Plan.updated_ts,
        Plan.total_indices,
        Plan.last_index,
        Plan.last_area_calculation_status,
        Plan.last_area_calculation_retries,
        Plan.calculated_ts,
        Plan.calculation_updated_ts,
        Plan.calculation_status,
    ]

    result = await db_session.execute(
        select(Plan).filter_by(ui_id=ui_id).options(load_only(*cols))
    )
    plan = result.scalars().first()

    return plan if plan else None


async def get_plan_with_report_areas_by_ui_id(
    db_session: AsyncSession, ui_id: UUID
) -> Optional[Plan]:
    cols = [
        Plan.id,
        Plan.ui_id,
        Plan.name,
        Plan.user_id,
        Plan.created_ts,
        Plan.updated_ts,
        Plan.total_indices,
        Plan.last_index,
        Plan.last_area_calculation_status,
        Plan.last_area_calculation_retries,
        Plan.calculated_ts,
        Plan.calculation_updated_ts,
        Plan.calculation_status,
        Plan.report_areas,
    ]

    result = await db_session.execute(
        select(Plan).filter_by(ui_id=ui_id).options(load_only(*cols))
    )
    plan = result.scalars().first()

    return plan if plan else None


async def get_plan_by_ui_id(db_session: AsyncSession, ui_id: UUID) -> Optional[Plan]:
    result = await db_session.execute(select(Plan).filter_by(ui_id=ui_id))
    plan = result.scalars().first()

    return plan if plan else None


async def get_all_plans(db_session: AsyncSession) -> List[Plan]:
    result = await db_session.execute(select(Plan))
    return result.scalars().all()


async def get_plan_ids_by_user_id(db_session: AsyncSession, user_id: str) -> List[str]:
    raw_sql = """
        SELECT ui_id
        FROM plan
        WHERE user_id = :user_id
        """

    result = await db_session.execute(
        text(raw_sql),
        {
            "user_id": user_id,
        },
    )
    features = result.fetchall()

    ids = []
    for feature in features:
        ids.append(feature[0])

    return ids


async def create_plan(db_session: AsyncSession, plan: Plan) -> Plan:
    db_session.add(plan)
    await db_session.commit()
    await db_session.refresh(plan)
    return plan


async def update_plan(db_session: AsyncSession, plan: Plan) -> bool:
    if not plan:
        return False

    await db_session.merge(plan)
    await db_session.commit()
    return True


async def delete_plan(db_session: AsyncSession, calc_id: UUID) -> bool:
    plan = await get_plan_by_id(db_session, calc_id)
    if not plan:
        return False

    await db_session.execute(delete(Plan).filter_by(id=calc_id))
    await db_session.commit()
    return True


async def get_feature_from_plan_by_ui_id(
    db_session: AsyncSession, ui_id: UUID, feature_id: str
) -> Optional[Any]:
    raw_sql = """
        SELECT feature
        FROM (
            SELECT jsonb_array_elements(data->'features') AS feature
            FROM plan
            WHERE ui_id = :plan_id
        ) sub
        WHERE feature->'properties'->>'id' = :feature_id
        """

    result = await db_session.execute(
        text(raw_sql), {"plan_id": ui_id, "feature_id": feature_id}
    )
    features = result.fetchall()

    # Return the first matching feature or None
    return features[0][0] if len(features) else None


async def get_feature_from_plan_by_ui_id_and_index(
    db_session: AsyncSession, ui_id: UUID, feature_index: int
) -> Optional[Any]:
    raw_sql = """
        SELECT (data->'features'->CAST(:feature_index as INTEGER)) AS feature
        FROM plan
        WHERE ui_id = :plan_id
        """

    result = await db_session.execute(
        text(raw_sql), {"plan_id": ui_id, "feature_index": feature_index}
    )
    features = result.fetchall()

    # Return the first matching feature or None
    return features[0][0] if len(features) else None


async def add_feature_to_plan_areas(
    db_session: AsyncSession, plan_id: UUID, feature: dict
) -> None:
    # Convert the feature dict to JSON
    feature_json = json.dumps(feature)

    # SQL to update the 'areas' field by appending the new feature
    raw_sql = """
        UPDATE plan
        SET areas = areas || :feature_json::jsonb
        WHERE id = :plan_id
    """

    # Execute the SQL command
    await db_session.execute(
        text(raw_sql), {"plan_id": plan_id, "feature_json": feature_json}
    )

    # Commit the changes
    await db_session.commit()


async def add_feature_collection_to_plan_areas(
    db_session: AsyncSession, plan_id: UUID, new_feature_collection_json: str
) -> None:
    # Convert the new feature collection dict to JSON
    new_features_jsonb = f"'{new_feature_collection_json}'::jsonb->'features'"

    # SQL to update the 'areas' field by merging the new feature collection
    raw_sql = f"""
                UPDATE plan
                SET report_areas = jsonb_set(
                    report_areas, 
                    '{{features}}', 
                    COALESCE(report_areas->'features', '[]'::jsonb) || COALESCE({new_features_jsonb}, '[]'::jsonb)
                )
                WHERE id = :plan_id
                """

    # Execute the SQL command
    await db_session.execute(
        text(raw_sql),
        {
            "plan_id": plan_id,
        },
    )

    # Commit the changes
    await db_session.commit()
