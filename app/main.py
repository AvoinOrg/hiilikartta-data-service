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

from app.calculator.calculator import CarbonCalculator
from app.types.general import CalculationStatus
from app.db.connection import get_state_db, get_gis_db
from app.calculator.calculator import CarbonCalculator
from app.db.plan import (
    update_plan_status,
    get_plan_by_id,
    create_plan,
)  # Import the methods from plan.py
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


async def background_calculation(
    file, zoning_col, state_db_session, gis_db_session, calc_id
):
    cc = CarbonCalculator(file, zoning_col, gis_db_session)
    data = await cc.calculate()

    if data == None:
        await update_plan_status(
            state_db_session,
            calc_id,
            CalculationStatus.ERROR.value,
            {"message": "No data found for polygons."},
        )
    else:
        await update_plan_status(
            state_db_session, calc_id, CalculationStatus.FINISHED.value, data
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
    id: str = Form(...),
    state_db_session: AsyncSession = Depends(get_state_db),
    gis_db_session: AsyncSession = Depends(get_gis_db),
):
    plan = await get_plan_by_id(state_db_session, id)
    if plan:
        if plan.calculation_status in [CalculationStatus.PROCESSING.value]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A calculation with the provided ID is already in progress.",
            )
        await update_plan_status(
            state_db_session, id, CalculationStatus.PROCESSING.value
        )
    else:
        await create_plan(state_db_session, id, CalculationStatus.PROCESSING.value)

    background_tasks.add_task(
        background_calculation,
        file.file,
        zoning_col,
        state_db_session,
        gis_db_session,
        id,
    )
    return {"status": CalculationStatus.PROCESSING.value, "id": id}


@app.get("/calculation")
async def get_calculation_status(
    request: Request, state_db_session: AsyncSession = Depends(get_state_db)
):
    calc_id: str = request.query_params.get("id")
    plan = await get_plan_by_id(state_db_session, calc_id)

    headers = {"Content-Encoding": "gzip"}

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Calculation not found."
        )

    if plan.calculation_status == CalculationStatus.PROCESSING.value:
        return Response(
            content=await zip_response_data(plan.data),
            headers=headers,
            status_code=status.HTTP_202_ACCEPTED,
        )

    if plan.calculation_status == CalculationStatus.ERROR.value:
        return Response(
            content=await zip_response_data(plan.data),
            headers=headers,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    if plan.calculation_status == CalculationStatus.FINISHED.value:
        return Response(
            content=await zip_response_data(plan.data),
            status_code=status.HTTP_200_OK,
            headers=headers,
        )

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected calculation status.",
    )
