from fastapi import FastAPI, Depends, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

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
    calcs = await cc.calculate()
    return {"sum": calcs.get("sum"), "area": calcs.get("area")}
