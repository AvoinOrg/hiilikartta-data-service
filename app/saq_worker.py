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

    print("we be starting NOW")
    time.sleep(60)

    try:
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
                    plan.last_area_calculation_status = (
                        CalculationStatus.PROCESSING.value
                    )
                    plan.last_area_calculation_retries = (
                        plan.last_area_calculation_retries + 1
                    )

                    await update_plan(
                        state_db_session,
                        plan,
                    )

        if feature:
            async with get_async_context_gis_db() as gis_db_session:
                try:
                    cc = CarbonCalculator(
                        {"type": "FeatureCollection", "features": [feature]},
                    )
                    calc_data = await cc.calculate(gis_db_session)
                except Exception as e:
                    logger.error(
                        f"Error calculating plan with ui_id: {plan.ui_id} on feature: {feature}"
                    )
                    logger.error(e)
                    if plan.last_area_calculation_retries > MAX_CALC_RETRIES:
                        plan.last_area_calculation_retries = 0
                        plan.last_index = plan.last_index + 1
                        await update_plan(
                            state_db_session,
                            plan,
                        )
                    elif feature:
                        plan.last_area_calculation_status = (
                            CalculationStatus.PROCESSING.value
                        )
                        plan.last_area_calculation_retries = (
                            plan.last_area_calculation_retries + 1
                        )

                    await update_plan(
                        state_db_session,
                        plan,
                    )

            async with get_async_context_state_db() as state_db_session:
                plan = await get_plan_without_data_by_ui_id(
                    state_db_session, UUID(ui_id)
                )
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

        await queue.enqueue(
            "calculate_piece", ui_id=str(ui_id), retries=0, timeout=172800
        )

    except Exception as e:
        logger.error(e)
        await queue.enqueue(
            "calculate_piece", ui_id=str(ui_id), retries=0, timeout=172800
        )


async def calculate_totals(ctx, *, ui_id: str):
    pass


async def handle_finished_calcs(ctx):
    r = redis.Redis(
        host="redis",  # or your Redis server's hostname
        port=6379,  # default Redis port
    )
    stats_data = {}

    for key in r.scan_iter("saq:job:default:*"):
        value = r.get(key)

        # Decode the key (if necessary) and store the retrieved data
        decoded_key = key.decode("utf-8")
        stats_data[decoded_key] = value

    for key, value in stats_data.items():
        data_str = value.decode("utf-8")
        data_dict = json.loads(data_str)

        if data_dict["function"] == "calculate_piece":
            if data_dict["status"] == "active":
                if data_dict["touched"] < time.time() - 120:
                    await queue.enqueue(
                        "calculate_piece",
                        ui_id=data_dict["kwargs"]["ui_id"],
                        scheduled=time.time() + 120,
                    )
            if data_dict["status"] == "complete":
                r.delete(key)  # Remove the item from Redis
            elif data_dict["status"] == "failed":
                async with get_async_context_state_db() as state_db_session:
                    ui_id = data_dict["kwargs"]["ui_id"]
                    plan = await get_plan_without_data_by_ui_id(
                        state_db_session, UUID(ui_id)
                    )
                    if plan:
                        plan.calculation_status = CalculationStatus.ERROR.value
                        await update_plan(
                            state_db_session,
                            plan,
                        )
                        r.delete(key)  # Remove the item from Redis


async def startup(ctx):
    lock_key = "startup_lock"
    lock_value = str(uuid.uuid4())
    r = redis.Redis(host="redis", port=6379)

    # Attempt to acquire the lock
    if r.set(lock_key, lock_value, ex=120, nx=True):
        try:
            logger.error("starting up")
            stats_data = {}

            for key in r.scan_iter("saq:job:default:*"):
                try:
                    value = r.get(key)
                    decoded_key = key.decode("utf-8")
                    stats_data[decoded_key] = value

                    for key, value in stats_data.items():
                        data_str = value.decode("utf-8")
                        data_dict = json.loads(data_str)

                        if (
                            data_dict["status"] == "active"
                            and data_dict["function"] == "calculate_piece"
                        ):
                            await queue.enqueue(
                                "calculate_piece",
                                ui_id=data_dict["kwargs"]["ui_id"],
                                scheduled=time.time() + 120,
                            )
                            await r.delete(key)
                except Exception as e:
                    # Handle exceptions appropriately
                    pass
        finally:
            # Ensure the lock is released by the process that acquired it
            if r.get(lock_key) == lock_value:
                r.delete(lock_key)
    else:
        # Handle the case where the lock could not be acquired
        logger.error("Lock is already held, skipping startup.")


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
