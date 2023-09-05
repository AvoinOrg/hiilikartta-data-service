from fastapi import FastAPI, Depends, UploadFile, Form, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
import gzip
import json

from app.database.database import get_db
from app.calculator.calculator import CarbonCalculator

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


@app.post("/calculate")
async def calculate(
    file: UploadFile = Form(...),
    zoning_col: str = Form(...),
    db_session: AsyncSession = Depends(get_db),
):
    cc = CarbonCalculator(file.file, zoning_col, db_session)
    data = await cc.calculate()

    # This probably no longer works. Figure out a better way to check for empty data.
    if data == None:
        raise HTTPException(status_code=400, detail="No data found for polygons.")

    json_str = json.dumps(data)

    # Gzipping the JSON string
    json_bytes = json_str.encode("utf-8")
    gzipped_json = gzip.compress(json_bytes)

    headers = {"Content-Encoding": "gzip", "Content-Type": "application/json"}

    return Response(content=gzipped_json, headers=headers)
