"""Audit log API (Finance and Super Admin)."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import DbSession
from app.core.pagination import Page, Pagination, paginate
from app.modules.accounts.deps import require
from app.modules.accounts.models import Invitation, User
from app.modules.accounts.service import display_names
from app.modules.audit.models import AuditLog
from app.modules.audit.schemas import AuditLogOut
from app.modules.fund_requests.models import FundRequest
from app.modules.projects.models import Project

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
    names = display_names(db, (r.actor_id for r in rows))
    subjects = _subject_labels(db, rows)
    items = [
        AuditLogOut.model_validate(r).model_copy(
            update={
                "actor_name": names.get(r.actor_id),
                "subject_label": subjects.get((r.entity, r.entity_id)),
            }
        )
        for r in rows
    ]
    return Page[AuditLogOut](items=items, total=total, page=params.page, size=params.size)


def _subject_labels(db: Session, rows: list[AuditLog]) -> dict[tuple[str, int], str]:
    """Readable names for what each row is about, one query per entity type on the page."""
    ids: dict[str, set[int]] = {}
    for r in rows:
        ids.setdefault(r.entity, set()).add(r.entity_id)

    labels: dict[tuple[str, int], str] = {}
    if project_ids := ids.get("projects"):
        for pid, code, name in db.execute(
            select(Project.id, Project.code, Project.name).where(Project.id.in_(project_ids))
        ):
            labels[("projects", pid)] = f"{code} · {name}"
    if user_ids := ids.get("users"):
        for uid, full_name in display_names(db, user_ids).items():
            labels[("users", uid)] = full_name
    if invitation_ids := ids.get("invitations"):
        for iid, email in db.execute(
            select(Invitation.id, Invitation.email).where(Invitation.id.in_(invitation_ids))
        ):
            labels[("invitations", iid)] = email
    if request_ids := ids.get("fund_requests"):
        for rid, ref_no, code in db.execute(
            select(FundRequest.id, FundRequest.ref_no, Project.code)
            .join(Project, Project.id == FundRequest.project_id)
            .where(FundRequest.id.in_(request_ids))
        ):
            labels[("fund_requests", rid)] = f"{ref_no} · {code}"
    return labels
