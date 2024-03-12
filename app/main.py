import shutil
import tempfile
from fastapi import (
    FastAPI,
    Depends,
    UploadFile,
    Form,
    HTTPException,
    status,
    Response,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
import gzip
import json
from uuid import UUID
from contextlib import asynccontextmanager
import geopandas as gpd
from typing import Dict, Any
import datetime

from app.types.general import CalculationStatus
from app.db.connection import get_async_context_gis_db, get_async_state_db
from app.db.plan import (
    get_plan_ids_by_user_id,
    update_plan,
    get_plan_by_ui_id,
    create_plan,
)  # Import the methods from plan.py
from app.db.models.plan import Plan
from app.utils.logger import get_logger
from app.utils.data_loader import load_area_multipliers, load_bm_curves, unload_files
from app.saq_worker import queue
from app.auth.validator import ZitadelIntrospectTokenValidator, ValidatorError

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup event
    load_area_multipliers()
    load_bm_curves()

    yield

    unload_files()


app = FastAPI(lifespan=lifespan)

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

# Define OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


# Function to validate the token and extract the user information
async def get_current_user(token: str = Depends(oauth2_scheme)):
    validator = ZitadelIntrospectTokenValidator()
    user_id = None

    try:
        data = validator.introspect_token(token)
        validator.validate_token(data, "")
        user_id = data.get("sub")
    except ValidatorError as ex:
        raise HTTPException(status_code=ex.status_code, detail=ex.error)

    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"user_id": user_id}


def process_and_create_plan(file, ui_id, name, plan=None):
    # Use a temporary file to process the data
    temp_file_path = None
    with tempfile.NamedTemporaryFile(
        delete=True, suffix=f"{ui_id}.zip", dir="/tmp"
    ) as temp_file:
        shutil.copyfileobj(file.file, temp_file)
        temp_file_path = temp_file.name
        temp_file.flush()
        data = gpd.read_file(temp_file_path, index_col="id")
        data.set_crs("EPSG:4326", inplace=True, allow_override=True)

        data = data[
            data.geometry.notna()
            & data.geometry.apply(
                lambda geom: geom.geom_type in ["Polygon", "MultiPolygon"]
            )
        ]
        data["is_valid"] = data["geometry"].is_valid
        # Fixing invalid geometries with buffer(0)
        data.loc[~data["is_valid"], "geometry"] = data.loc[
            ~data["is_valid"], "geometry"
        ].apply(lambda geom: geom.buffer(0))
        data = data[data.geometry.is_valid]
        data.drop(columns=["is_valid"], inplace=True)

        total_indices = len(data)
        data = data.to_json()

        if plan:
            plan.data = data
            plan.total_indices = total_indices
            plan.last_index = -1
            plan.last_area_calculation_retries = 0
            plan.last_saved = datetime.datetime.utcnow()

            return plan

        else:
            new_plan = Plan(
                ui_id=ui_id,
                name=name,
                calculation_status=CalculationStatus.PROCESSING.value,
                data=data,
                total_indices=total_indices,
                last_index=-1,
                last_area_calculation_retries=0,
                report_areas=json.dumps({"type": "FeatureCollection", "features": []}),
                report_totals=None,
                calculated_ts=None,
                last_area_calculation_status=None,
                last_saved=datetime.datetime.utcnow(),
            )

            return new_plan


async def zip_response_data(data):
    json_str = json.dumps(data)
    json_bytes = json_str.encode("utf-8")
    gzipped_json = gzip.compress(json_bytes)

    return gzipped_json


@app.post("/calculation")
async def calculate(
    request: Request,
    file: UploadFile = Form(...),
    user_id: str = Form(None),
    state_db_session: AsyncSession = Depends(get_async_state_db),
):
    try:
        ui_id: UUID = UUID(request.query_params.get("id"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided ID is not a valid UUID.",
        )

    name = request.query_params.get("name")
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name parameter is missing.",
        )

    plan = await get_plan_by_ui_id(state_db_session, ui_id)

    if plan and plan.calculation_status.value == CalculationStatus.PROCESSING.value:
        if plan.calculation_status.value == CalculationStatus.PROCESSING.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A calculation with the provided ID is already in progress.",
            )
        plan.calculation_status = CalculationStatus.PROCESSING
        await update_plan(state_db_session, plan)
    else:
        temp_file_path = None
        new_plan = process_and_create_plan(file, ui_id, name)

        if plan:
            new_plan.id = plan.id
            await update_plan(state_db_session, new_plan)
        else:
            await create_plan(
                state_db_session, new_plan
            )  # Pass the new plan to create_plan function

    await queue.enqueue("calculate_piece", ui_id=str(ui_id), retries=3, timeout=172800)

    return {"status": CalculationStatus.PROCESSING.value, "id": ui_id}


@app.get("/calculation")
async def get_calculation_status(
    request: Request, state_db_session: AsyncSession = Depends(get_async_state_db)
):
    try:
        ui_id: UUID = UUID(request.query_params.get("id"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided ID is not a valid UUID.",
        )
    plan = await get_plan_by_ui_id(state_db_session, ui_id)

    headers = {"Content-Encoding": "gzip"}

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Calculation not found."
        )

    content = {"status": plan.calculation_status.value, "id": str(ui_id)}

    if plan.calculation_status.value == CalculationStatus.PROCESSING.value:
        return Response(
            content=await zip_response_data(content),
            headers=headers,
            status_code=status.HTTP_202_ACCEPTED,
        )

    if plan.calculation_status.value == CalculationStatus.ERROR.value:
        return Response(
            content=await zip_response_data(content),
            headers=headers,
            status_code=status.HTTP_206_PARTIAL_CONTENT,
        )

    if plan.calculation_status.value == CalculationStatus.FINISHED.value:
        content["data"] = {
            "totals": plan.report_totals,
            "areas": plan.report_areas,
            "metadata": {
                "report_name": plan.name,
                "calculated_ts": (
                    int(plan.calculated_ts.timestamp()) if plan.calculated_ts else None
                ),
            },
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


@app.get("/plan/external")
async def get_plan_external(
    request: Request, state_db_session: AsyncSession = Depends(get_async_state_db)
):
    try:
        ui_id: UUID = UUID(request.query_params.get("id"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided ID is not a valid UUID.",
        )
    plan = await get_plan_by_ui_id(state_db_session, ui_id)

    headers = {"Content-Encoding": "gzip"}

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Calculation not found."
        )

    content: Dict[str, Any] = {
        "id": str(ui_id),
        "name": plan.name,
    }

    if plan.calculation_status.value == CalculationStatus.FINISHED.value:
        content["report_data"] = {
            "totals": plan.report_totals,
            "areas": plan.report_areas,
            "metadata": {
                "calculated_ts": (
                    int(plan.calculated_ts.timestamp()) if plan.calculated_ts else None
                ),
            },
        }

    return Response(
        content=await zip_response_data(content),
        status_code=status.HTTP_200_OK,
        headers=headers,
    )
