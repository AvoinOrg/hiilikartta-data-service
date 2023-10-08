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
import uuid

from app.database.database import get_db
from app.calculator.calculator import CarbonCalculator
from app.types.calculator import CalculationStatus

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

calculations = {}


async def background_calculation(file, zoning_col, db_session, calc_id):
    cc = CarbonCalculator(file, zoning_col, db_session)
    data = await cc.calculate()

    if data == None:
        calculations[calc_id] = {
            "status": CalculationStatus.ERROR.value,
            "message": "No data found for polygons.",
        }
    else:
        calculations[calc_id] = {
            "status": CalculationStatus.COMPLETED.value,
            "data": data,
        }


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
    db_session: AsyncSession = Depends(get_db),
):
    calc_id = str(uuid.uuid4())
    calculations[calc_id] = {"status": CalculationStatus.PROCESSING.value}
    background_tasks.add_task(
        background_calculation, file.file, zoning_col, db_session, calc_id
    )
    return {"status": CalculationStatus.STARTED.value, "id": calc_id}


@app.get("/calculation")
async def get_calculation_status(calc_id: str = Request.query_params["id"]):
    data = calculations.get(calc_id)

    headers = {"Content-Encoding": "gzip"}

    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Calculation not found."
        )

    if data["status"] == CalculationStatus.PROCESSING.value:
        return Response(
            content=await zip_response_data(data),
            headers=headers,
            status_code=status.HTTP_202_ACCEPTED,
        )

    if data["status"] == CalculationStatus.ERROR.value:
        return Response(
            content=await zip_response_data(data),
            headers=headers,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    if data["status"] == CalculationStatus.COMPLETED.value:
        return Response(
            content=await zip_response_data(data),
            status_code=status.HTTP_200_OK,
            headers=headers,
        )

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Unexpected calculation status.",
    )
