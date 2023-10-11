from fastapi import (
    FastAPI,
    Depends,
    UploadFile,
    Form,
    HTTPException,
    BackgroundTasks,
    status,
    Response,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
import gzip
import json
from uuid import UUID
from datetime import datetime

from app.calculator.calculator import CarbonCalculator
from app.types.general import CalculationStatus
from app.db.connection import get_async_context_gis_db, get_async_state_db
from app.calculator.calculator import CarbonCalculator
from app.db.plan import (
    update_plan,
    get_plan_by_ui_id,
    create_plan,
)  # Import the methods from plan.py
from app.db.models.plan import Plan
from app.utils.logger import get_logger

logger = get_logger(__name__)
app = FastAPI()

origins = [
    "*",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def background_calculation(file, zoning_col, state_db_session, ui_id):
    async with get_async_context_gis_db() as gis_db_session:
        cc = CarbonCalculator(file, zoning_col)
        calc_data = await cc.calculate(gis_db_session)

        plan = await get_plan_by_ui_id(state_db_session, ui_id)
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
            plan.calculated_ts = datetime.utcnow()
            plan.calculation_status = CalculationStatus.FINISHED.value
            await update_plan(
                state_db_session,
                plan,
            )


async def zip_response_data(data):
    json_str = json.dumps(data)
    json_bytes = json_str.encode("utf-8")
    gzipped_json = gzip.compress(json_bytes)

    return gzipped_json


@app.post("/calculation")
async def calculate(
    background_tasks: BackgroundTasks,
    file: UploadFile = Form(...),
    zoning_col: str = Form(...),
    id: str = Form(...),  # This id is a string
    user_id: str = Form(None),
    state_db_session: AsyncSession = Depends(get_async_state_db),
):
    try:
        ui_id = UUID(id)  # Convert string id to UUID
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided ID is not a valid UUID.",
        )

    plan = await get_plan_by_ui_id(state_db_session, ui_id)
    if plan:
        if plan.calculation_status == CalculationStatus.PROCESSING.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A calculation with the provided ID is already in progress.",
            )
        plan.calculation_status = CalculationStatus.PROCESSING.value
        await update_plan(state_db_session, plan)
    else:
        new_plan = Plan(
            ui_id=ui_id,
            calculation_status=CalculationStatus.PROCESSING.value,
        )
        if user_id:  # or any other condition to validate user_id
            new_plan["user_id"] = user_id
        await create_plan(
            state_db_session, new_plan
        )  # Pass the new plan to create_plan function

    background_tasks.add_task(
        background_calculation,
        file.file,
        zoning_col,
        state_db_session,
        ui_id,
    )
    return {"status": CalculationStatus.PROCESSING.value, "id": ui_id}


@app.get("/calculation")
async def get_calculation_status(
    request: Request, state_db_session: AsyncSession = Depends(get_async_state_db)
):
    ui_id: UUID = UUID(request.query_params.get("id"))
    plan = await get_plan_by_ui_id(state_db_session, ui_id)

    headers = {"Content-Encoding": "gzip"}

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Calculation not found."
        )

    content = {"status": plan.calculation_status.value, "id": str(ui_id)}

    if plan.calculation_status == CalculationStatus.PROCESSING:
        return Response(
            content=await zip_response_data(content),
            headers=headers,
            status_code=status.HTTP_202_ACCEPTED,
        )

    if plan.calculation_status == CalculationStatus.ERROR:
        return Response(
            content=await zip_response_data(content),
            headers=headers,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    if plan.calculation_status == CalculationStatus.FINISHED:
        content["data"] = {
            "totals": plan.report_totals,
            "areas": plan.report_areas,
        }
        return Response(
            content=await zip_response_data(content),
            status_code=status.HTTP_200_OK,
            headers=headers,
        )

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected calculation status.",
    )
