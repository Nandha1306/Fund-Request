"""Audit log API (Finance and Super Admin)."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter
from sqlalchemy import select

from app.core.db import DbSession
from app.core.pagination import Page, Pagination, paginate
from app.modules.accounts.deps import require
from app.modules.accounts.models import User
from app.modules.audit.models import AuditLog
from app.modules.audit.schemas import AuditLogOut

router = APIRouter(tags=["audit"])


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


@router.get("/audit", response_model=Page[AuditLogOut])
def list_audit(
    db: DbSession,
    params: Pagination,
    _: Annotated[User, require("audit.view")],
    entity: str | None = None,
    entity_id: int | None = None,
    actor_id: int | None = None,
    action: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[AuditLogOut]:
    stmt = select(AuditLog)
    if entity is not None:
        stmt = stmt.where(AuditLog.entity == entity)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    if date_from is not None:
        stmt = stmt.where(AuditLog.created_at >= _start_of(date_from))
    if date_to is not None:  # inclusive: the whole of date_to
        stmt = stmt.where(AuditLog.created_at < _start_of(date_to + timedelta(days=1)))
    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())

    rows, total = paginate(db, stmt, params)
    return Page[AuditLogOut](
        items=[AuditLogOut.model_validate(r) for r in rows], total=total, page=params.page, size=params.size
    )
