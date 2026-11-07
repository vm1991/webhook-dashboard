from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app import db, deliveries, events, seed, sim
from app.config import get_max_attempts

INDEX_HTML = Path(__file__).parent / "static" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    get_max_attempts()  # fail at startup on an invalid MAX_ATTEMPTS
    seed.seed_if_empty()
    yield


app = FastAPI(title="Webhook Delivery Dashboard", lifespan=lifespan)
app.include_router(events.router)
app.include_router(deliveries.router)
app.include_router(sim.router)
app.include_router(seed.router)


@app.exception_handler(OperationalError)
async def database_unavailable(request: Request, exc: OperationalError):
    # Only connectivity-type errors; other DB errors (e.g. IntegrityError) stay 500.
    # No exception details in the body.
    return JSONResponse(status_code=503, content={"error": "database_unavailable"})


@app.get("/")
def root():
    return FileResponse(INDEX_HTML, media_type="text/html")


@app.get("/health")
def health():
    try:
        with db.SessionLocal() as session:
            now = session.execute(text("SELECT now()")).scalar_one()
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok", "db_time": now.isoformat()}
