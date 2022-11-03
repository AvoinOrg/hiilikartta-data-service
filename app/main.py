from fastapi import FastAPI, File, UploadFile
from app.calculator.calculator import CarbonCalculator

app = FastAPI()

cc = CarbonCalculator()

@app.post("/uploadfile/")
async def create_upload_file(file: UploadFile):
    return {"filename": file.filename}


@app.get("/calc")
async def root():
    sum = cc.calculate("data/vantaa_yk.shp")
    return sum
