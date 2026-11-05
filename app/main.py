from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Webhook Delivery Dashboard", lifespan=lifespan)


@app.get("/")
def root():
    return {"message": "Hello World"}


@app.get("/health")
def health():
    try:
        with db.SessionLocal() as session:
            now = session.execute(text("SELECT now()")).scalar_one()
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok", "db_time": now.isoformat()}
