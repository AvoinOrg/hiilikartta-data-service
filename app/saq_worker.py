from saq import Queue
from uuid import UUID

from app.calculator.calculator import CarbonCalculator
from app.types.general import CalculationStatus
from app.db.connection import get_async_context_gis_db, get_async_context_state_db
from app.calculator.calculator import CarbonCalculator
from app.db.plan import (
    add_feature_collection_to_plan_areas,
    get_feature_from_plan_by_ui_id_and_index,
    get_plan_with_report_areas_by_ui_id,
    get_plan_without_data_by_ui_id,
    update_plan,
    get_plan_by_ui_id,
)  # Import the methods from plan.py
from app import config
from app.utils.logger import get_logger

logger = get_logger(__name__)

global_settings = config.get_settings()

MAX_CALC_RETRIES = 2


# class CalculationResult(TypedDict):
#     areas: str
#     totals: str
#     metadata: str


async def calculate(ctx, *, ui_id: str):
    plan = None
    async with get_async_context_state_db() as state_db_session:
        plan = await get_plan_by_ui_id(state_db_session, UUID(ui_id))

    calc_data = None
    if plan:
        async with get_async_context_gis_db() as gis_db_session:
            cc = CarbonCalculator(plan.data)
            calc_data = await cc.calculate(gis_db_session)

        async with get_async_context_state_db() as state_db_session:
            plan = await get_plan_by_ui_id(state_db_session, UUID(ui_id))
            if calc_data == None:
                plan.calculation_status = CalculationStatus.ERROR.value
                await update_plan(
                    state_db_session,
                    plan,
                    CalculationStatus.ERROR.value,
                    {"message": "No data found for polygons."},
                )
            else:
                plan.report_areas = calc_data["areas"]
                plan.report_totals = calc_data["totals"]
                plan.calculated_ts = calc_data["metadata"].get("timestamp")
                plan.calculation_status = CalculationStatus.FINISHED.value
                await update_plan(
                    state_db_session,
                    plan,
                )


async def calculate_piece(ctx, *, ui_id: str):
    plan = None
    feature = None
    totals = None
    async with get_async_context_state_db() as state_db_session:
        plan = await get_plan_without_data_by_ui_id(state_db_session, UUID(ui_id))

        if not plan:
            raise ValueError("Plan not found or is invalid.")
        if plan:
            if plan.last_index + 1 >= plan.total_indices:
                plan_report = await get_plan_with_report_areas_by_ui_id(
                    state_db_session, UUID(ui_id)
                )
                if plan_report:
                    cc = CarbonCalculator(plan_report.report_areas, sort_col="none")
                    calc_data = await cc.calculate_totals()

                    if calc_data:
                        plan.calculation_status = CalculationStatus.FINISHED.value
                        plan.report_totals = calc_data["totals"]
                        plan.calculated_ts = calc_data["metadata"].get("timestamp")
                        await update_plan(
                            state_db_session,
                            plan,
                        )
                return

            feature = await get_feature_from_plan_by_ui_id_and_index(
                state_db_session, UUID(ui_id), plan.last_index + 1
            )
            if plan.last_area_calculation_retries > MAX_CALC_RETRIES:
                plan.last_area_calculation_retries = 0
                plan.last_index = plan.last_index + 1
                await update_plan(
                    state_db_session,
                    plan,
                )
            elif feature:
                plan.last_area_calculation_status = CalculationStatus.PROCESSING.value
                plan.last_area_calculation_retries = (
                    plan.last_area_calculation_retries + 1
                )

                await update_plan(
                    state_db_session,
                    plan,
                )

    if feature:
        async with get_async_context_gis_db() as gis_db_session:
            cc = CarbonCalculator(
                {"type": "FeatureCollection", "features": [feature]},
            )
            calc_data = await cc.calculate(gis_db_session)

        async with get_async_context_state_db() as state_db_session:
            plan = await get_plan_without_data_by_ui_id(state_db_session, UUID(ui_id))
            if calc_data == None:
                plan.calculation_status = CalculationStatus.ERROR.value
                plan.last_area_calculation_status = CalculationStatus.ERROR.value
                plan.last_area_calculation_retries = (
                    plan.last_area_calculation_retries + 1
                )
                await update_plan(
                    state_db_session,
                    plan,
                )
            else:
                await add_feature_collection_to_plan_areas(
                    state_db_session, plan.id, calc_data["areas"]
                )

                plan.last_area_calculation_status = CalculationStatus.FINISHED.value
                plan.calculation_updated_ts = calc_data["metadata"].get("timestamp")
                plan.last_index = plan.last_index + 1
                await update_plan(
                    state_db_session,
                    plan,
                )

    await queue.enqueue("calculate_piece", ui_id=str(ui_id), retries=3, timeout=172800)


async def calculate_totals(ctx, *, ui_id: str):
    pass


async def startup(ctx):
    # ctx["db"] = await create_db()
    pass


async def shutdown(ctx):
    # await ctx["db"].disconnect()
    pass


async def before_process(ctx):
    # print(ctx["job"], ctx["db"])
    pass


async def after_process(ctx):
    pass


queue = Queue.from_url(global_settings.redis_url)

settings = {
    "queue": queue,
    "functions": [calculate, calculate_piece],
    "concurrency": 10,
    # "startup": startup,
    # "shutdown": shutdown,
    # "before_process": before_process,
    # "after_process": after_process,
}

# try:
#     if more_work_to_do:
#         perform_calculation_part.delay(file, zoning_col, ui_id, next_part_info)
# except SoftTimeLimitExceeded:
#     # Handle the soft time limit exception
#     pass
# except Exception as e:
#     # Handle other exceptions and retry the task
#     try:
#         self.retry(countdown=60)  # Retries the task after 60 seconds
#     except self.MaxRetriesExceededError:
#         # Handle the situation when max retries have been exceeded
#         pass


# In your FastAPI route or background task
