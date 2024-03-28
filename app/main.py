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
    get_plan_stats_by_user_id,
    update_plan,
    get_plan_by_ui_id,
    create_plan,
    delete_plan,
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


async def get_current_user_optional(token: str = Depends(oauth2_scheme)):
    if not token:
        return None
    try:
        res = await get_current_user(token)
        return res
    except HTTPException as e:
        return None


def process_and_create_plan(file, ui_id, visible_ui_id, name, user_id=None, plan=None):
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
            plan.saved_ts = datetime.datetime.utcnow()

            if plan.user_id is None and user_id:
                plan.user_id = user_id

            return plan

        else:
            new_plan = Plan(
                ui_id=ui_id,
                visible_ui_id=visible_ui_id,
                name=name,
                calculation_status=CalculationStatus.NOT_STARTED.value,
                data=data,
                total_indices=total_indices,
                last_index=-1,
                last_area_calculation_retries=0,
                report_areas=json.dumps({"type": "FeatureCollection", "features": []}),
                report_totals=None,
                calculated_ts=None,
                last_area_calculation_status=None,
                saved_ts=datetime.datetime.now(),
                user_id=user_id,
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
    current_user: dict = Depends(get_current_user_optional),
    state_db_session: AsyncSession = Depends(get_async_state_db),
):
    try:
        ui_id: UUID = UUID(request.query_params.get("id"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided ID is not a valid UUID.",
        )

    visible_ui_id = request.query_params.get("visible_id")
    if not visible_ui_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="visible_id parameter is missing.",
        )

    name = request.query_params.get("name")
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name parameter is missing.",
        )

    plan = await get_plan_by_ui_id(state_db_session, ui_id)

    if plan and plan.calculation_status.value == CalculationStatus.PROCESSING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A calculation with the provided ID is already in progress.",
        )

    else:
        user_id = current_user.get("user_id")
        new_plan = process_and_create_plan(file, ui_id, visible_ui_id, name, user_id)
        new_plan.calculation_status = CalculationStatus.PROCESSING

        if plan:
            new_plan.id = plan.id
            await update_plan(state_db_session, new_plan)
        else:
            await create_plan(
                state_db_session, new_plan
            )  # Pass the new plan to create_plan function

        await queue.enqueue(
            "calculate_piece", ui_id=str(ui_id), retries=3, timeout=172800
        )

        return {
            "status": CalculationStatus.PROCESSING.value,
            "id": ui_id,
            "user_id": user_id,
            "saved_ts": new_plan.saved_ts.timestamp(),
        }


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

    content = {
        "id": str(ui_id),
        "calculation_status": plan.calculation_status.value,
        "calculation_updated_ts": (
            plan.calculation_updated_ts.timestamp()
            if plan.calculation_updated_ts
            else None
        ),
        "total_indices": plan.total_indices,
        "last_index": plan.last_index,
    }

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


@app.put("/plan")
async def create_update_plan(
    request: Request,
    file: UploadFile = Form(...),
    current_user: dict = Depends(get_current_user),
    state_db_session: AsyncSession = Depends(get_async_state_db),
):
    user_id = current_user.get("user_id")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        ui_id: UUID = UUID(request.query_params.get("id"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided ID is not a valid UUID.",
        )

    visible_ui_id = request.query_params.get("visible_id")
    if not visible_ui_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="visible_id parameter is missing.",
        )

    name = request.query_params.get("name")
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name parameter is missing.",
        )

    plan = await get_plan_by_ui_id(state_db_session, ui_id)

    if plan:
        plan = process_and_create_plan(file, ui_id, visible_ui_id, name, plan=plan)

        await update_plan(state_db_session, plan)

        return JSONResponse(
            content={
                "id": str(ui_id),
                "user_id": user_id,
                "saved_ts": plan.saved_ts.timestamp(),
            },
            status_code=status.HTTP_200_OK,
        )
    else:
        new_plan = process_and_create_plan(file, ui_id, visible_ui_id, name, user_id)

        await create_plan(
            state_db_session, new_plan
        )  # Pass the new plan to create_plan function

        return JSONResponse(
            content={
                "id": str(ui_id),
                "user_id": user_id,
                "saved_ts": new_plan.saved_ts.timestamp(),
            },
            status_code=status.HTTP_201_CREATED,
        )


@app.get("/plan")
async def get_plan(
    request: Request,
    current_user: dict = Depends(get_current_user),
    state_db_session: AsyncSession = Depends(get_async_state_db),
):
    user_id = current_user.get("user_id")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        ui_id: UUID = UUID(request.query_params.get("id"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided ID is not a valid UUID.",
        )

    plan = await get_plan_by_ui_id(state_db_session, ui_id)

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found."
        )

    if plan.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Plan does not belong to the user.",
        )

    headers = {"Content-Encoding": "gzip"}

    content: Dict[str, Any] = {
        "id": str(ui_id),
        "visible_id": plan.visible_ui_id,
        "name": plan.name,
        "data": plan.data,
        "user_id": plan.user_id,
        "saved_ts": plan.saved_ts.timestamp(),
        "created_ts": plan.created_ts.timestamp(),
        "calculation_status": plan.calculation_status.value,
        "calculation_updated_ts": (
            plan.calculation_updated_ts.timestamp()
            if plan.calculation_updated_ts
            else None
        ),
        "total_indices": plan.total_indices,
        "last_index": plan.last_index,
    }

    if plan.calculated_ts is not None and plan.report_totals is not None:
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


@app.delete("/plan")
async def del_plan(
    request: Request,
    current_user: dict = Depends(get_current_user),
    state_db_session: AsyncSession = Depends(get_async_state_db),
):
    try:
        ui_id: UUID = UUID(request.query_params.get("id"))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The provided ID is not a valid UUID.",
        )

    plan = await get_plan_by_ui_id(state_db_session, ui_id)

    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found."
        )

    if plan.user_id:
        user_id = current_user.get("user_id")

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if plan.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Plan does not belong to the user.",
            )

    was_deleted = await delete_plan(state_db_session, plan.id)

    if not was_deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The plan could not be deleted.",
        )

    return Response(status_code=status.HTTP_200_OK)


@app.get("/user/plans")
async def get_user_plans(
    current_user: dict = Depends(get_current_user),
    state_db_session: AsyncSession = Depends(get_async_state_db),
):
    user_id = current_user.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    plan_stats = await get_plan_stats_by_user_id(state_db_session, user_id)

    parsed_stats = []
    for stats in plan_stats:
        parsed_stats.append(
            {
                "id": str(stats["ui_id"]),
                "visible_id": stats["visible_ui_id"],
                "name": stats["name"],
                "saved_ts": stats["saved_ts"].timestamp(),
            }
        )

    return {"stats": parsed_stats}
