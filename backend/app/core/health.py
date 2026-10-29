from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import DbSession

router = APIRouter(tags=["health"])


@router.get("/health")
def liveness() -> dict[str, str]:
    """The process is up. Does not touch the database."""
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(db: DbSession) -> JSONResponse:
    """The API can reach PostgreSQL; used by the load balancer before sending traffic."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "down"})
    return JSONResponse(content={"status": "ok", "database": "up"})
