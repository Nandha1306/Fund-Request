"""User administration and directory endpoints.

Route order matters: the fixed paths (`/users/invite`, `/users/invitations`,
`/users/approvers`, `/users/requestors`) are declared **before** `/users/{user_id}`,
otherwise the id path would swallow them.
"""

from typing import Annotated

from fastapi import APIRouter
from sqlalchemy import or_, select

from app.core.db import DbSession
from app.core.enums import Role
from app.core.pagination import Page, Pagination, paginate
from app.modules.accounts import service
from app.modules.accounts.deps import CurrentUser, require
from app.modules.accounts.models import User
from app.modules.accounts.schemas import (
    InvitationOut,
    InviteCreate,
    InviteOut,
    UpdateUserResponse,
    UserAdminOut,
    UserBrief,
    UserUpdate,
)

router = APIRouter(tags=["users"])

_Admin = Annotated[User, require("user.manage")]
_MemberEditor = Annotated[User, require("project.members")]


@router.post("/users/invite", response_model=InviteOut, status_code=201)
def invite_user(body: InviteCreate, db: DbSession, actor: _Admin) -> InviteOut:
    """Invite somebody.  The link is returned **once** — only its hash is stored."""
    invitation, link = service.invite_user(db, actor, body)
    return InviteOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        invite_link=link,
    )


@router.get("/users/invitations", response_model=list[InvitationOut])
def list_invitations(db: DbSession, actor: _Admin) -> list[InvitationOut]:
    """Pending invitations (no links: the raw tokens were never stored)."""
    return [InvitationOut.model_validate(inv) for inv in service.list_pending_invitations(db)]


@router.get("/users", response_model=Page[UserAdminOut])
def list_users(
    db: DbSession,
    actor: _Admin,
    params: Pagination,
    role: Role | None = None,
    is_active: bool | None = None,
    q: str | None = None,
):
    """All users with their flags and effective permissions."""
    stmt = select(User)
    if role is not None:
        stmt = stmt.where(User.role == Role(role).value)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.email.ilike(pattern), User.full_name.ilike(pattern)))
    stmt = stmt.order_by(User.created_at.desc(), User.id.desc())

    rows, total = paginate(db, stmt, params)
    return Page(
        items=[service.admin_out(u) for u in rows],
        total=total,
        page=params.page,
        size=params.size,
    )


@router.get("/users/approvers", response_model=list[UserBrief])
def list_approvers(db: DbSession, user: CurrentUser) -> list[UserBrief]:
    """Active approvers for the request form, excluding the caller (no self-approval)."""
    return [UserBrief.model_validate(u) for u in service.list_approvers(db, user)]


@router.get("/users/requestors", response_model=list[UserBrief])
def list_requestors(db: DbSession, actor: _MemberEditor) -> list[UserBrief]:
    """Active requestors for the project members picker."""
    return [UserBrief.model_validate(u) for u in service.list_requestors(db)]


@router.patch("/users/{user_id}", response_model=UpdateUserResponse)
def update_user(user_id: int, body: UserUpdate, db: DbSession, actor: _Admin) -> UpdateUserResponse:
    """Change role / active / finance-ops, with the guard rails; may report reassignments."""
    return service.update_user(db, actor, user_id, body)
