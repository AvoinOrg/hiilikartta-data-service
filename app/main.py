from fastapi import FastAPI, UploadFile
from fastapi.middleware.cors import CORSMiddleware

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

cc = CarbonCalculator()


@app.post("/calculate")
async def calculate(file: UploadFile):
    sum, area = cc.calculate(file.file)
    return {"sum": sum, "area": area}
