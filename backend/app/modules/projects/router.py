"""Project endpoints."""

from typing import Annotated

from fastapi import APIRouter
from sqlalchemy import or_, select

from app.core.db import DbSession
from app.core.enums import ProjectStatus
from app.core.pagination import Page, Pagination, paginate
from app.modules.accounts.deps import CurrentUser, require
from app.modules.accounts.models import User
from app.modules.finance.models import BudgetLedger
from app.modules.projects import policies, service
from app.modules.projects.models import Project
from app.modules.projects.schemas import (
    LedgerEntryOut,
    MemberOut,
    MembersUpdate,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
)

router = APIRouter(tags=["projects"])

_ProjectEditor = Annotated[User, require("project.manage")]
_MemberEditor = Annotated[User, require("project.members")]


def _page(items: list, total: int, params: Pagination) -> Page:
    return Page(items=items, total=total, page=params.page, size=params.size)


@router.get("/projects", response_model=Page[ProjectOut])
def list_projects(
    db: DbSession,
    user: CurrentUser,
    params: Pagination,
    status: ProjectStatus | None = None,
    q: str | None = None,
):
    """Projects the caller may see: all for Finance, eligible-only for Requestors."""
    stmt = policies.apply_visibility(select(Project).order_by(Project.code), user)
    if status is not None:
        stmt = stmt.where(Project.status == ProjectStatus(status).value)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Project.code.ilike(pattern), Project.name.ilike(pattern)))

    rows, total = paginate(db, stmt, params)
    return _page([ProjectOut.model_validate(r) for r in rows], total, params)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: DbSession, user: CurrentUser) -> ProjectOut:
    """Project detail with the four buckets and the derived ``available`` figure."""
    return ProjectOut.model_validate(policies.get_visible_project_or_404(db, user, project_id))


@router.post("/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, db: DbSession, actor: _ProjectEditor) -> ProjectOut:
    """Create a project; the initial allocation is written as an ALLOCATE ledger row."""
    return ProjectOut.model_validate(service.create_project(db, actor, body))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int, body: ProjectUpdate, db: DbSession, actor: _ProjectEditor
) -> ProjectOut:
    """Update name / description / status, or change the allocation through the budget engine."""
    return ProjectOut.model_validate(service.update_project(db, actor, project_id, body))


@router.get("/projects/{project_id}/members", response_model=list[MemberOut])
def get_members(project_id: int, db: DbSession, actor: _MemberEditor) -> list[MemberOut]:
    policies.get_project_or_404(db, project_id)
    return service.list_members(db, project_id)


@router.put("/projects/{project_id}/members", response_model=list[MemberOut])
def put_members(
    project_id: int, body: MembersUpdate, db: DbSession, actor: _MemberEditor
) -> list[MemberOut]:
    """Replace the member list with exactly these active requestor ids."""
    project = policies.get_project_or_404(db, project_id)
    return service.set_members(db, actor, project, body.user_ids)


@router.get("/projects/{project_id}/ledger", response_model=Page[LedgerEntryOut])
def get_ledger(project_id: int, db: DbSession, actor: _ProjectEditor, params: Pagination):
    """Append-only money trail of the project, newest first."""
    policies.get_project_or_404(db, project_id)
    stmt = select(BudgetLedger).where(BudgetLedger.project_id == project_id).order_by(
        BudgetLedger.created_at.desc(), BudgetLedger.id.desc()
    )
    rows, total = paginate(db, stmt, params)
    return _page([LedgerEntryOut.model_validate(r) for r in rows], total, params)
