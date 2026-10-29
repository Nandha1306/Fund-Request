"""User and invitation use cases.

Each function owns its transaction and ends with exactly one ``db.commit()``.
The raw invitation token exists only inside :func:`invite_user`: only its SHA-256 is
stored, and it is never written to an audit row, a log line or an error message.
"""

import hashlib
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.enums import RequestStatus, Role
from app.core.errors import AppError, BusinessRuleViolation, Conflict, NotFound
from app.core.security import hash_password
from app.modules.accounts.models import Invitation, User
from app.modules.accounts.permissions import permissions_for
from app.modules.accounts.schemas import (
    AcceptInvite,
    InviteCreate,
    UpdateUserResponse,
    UserAdminOut,
    UserUpdate,
)
from app.modules.audit import service as audit
from app.modules.fund_requests.models import FundRequest

# One message for unknown / expired / already-used tokens, so the API reveals nothing.
INVALID_INVITATION = "Invitation is invalid or has expired"

# "Non-terminal" = anything the request can still move on from (arch §7.2).
TERMINAL_STATUSES = (RequestStatus.LIQUIDATED.value, RequestStatus.WITHDRAWN.value)

_USER_SNAPSHOT_FIELDS = ["role", "is_active", "can_finance_ops"]


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _pending_invite_condition():
    return (Invitation.accepted_at.is_(None), Invitation.expires_at > _now())


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

def invite_user(db: Session, actor: User, data: InviteCreate) -> tuple[Invitation, str]:
    """Create (or refresh) an invitation and return the row plus its one-time link."""
    email = data.email

    # Any user holding that email, active or not — an inactive account is reactivated
    # with PATCH /api/users/{id}, not re-invited (otherwise accept would break the
    # unique-email constraint with a 500).
    if db.scalar(select(User.id).where(User.email == email)):
        raise Conflict("A user with this email already exists")

    # Re-inviting a pending email expires the old link, so only one link is ever valid.
    db.execute(
        update(Invitation)
        .where(Invitation.email == email, *_pending_invite_condition())
        .values(expires_at=_now())
    )

    token = secrets.token_urlsafe(32)
    settings = get_settings()
    invitation = Invitation(
        email=email,
        role=Role(data.role).value,
        token_hash=_hash_token(token),
        invited_by=actor.id,
        expires_at=_now() + timedelta(hours=settings.invite_ttl_hours),
    )
    db.add(invitation)
    db.flush()  # so the audit row can point at the invitation id

    audit.record(
        db,
        entity="invitations",
        entity_id=invitation.id,
        action="invited",
        actor_id=actor.id,
        snapshot=audit.snapshot_of(invitation, ["email", "role", "expires_at"]),  # JSON-safe; never the token
    )
    db.commit()

    link = f"{settings.frontend_base_url}/accept-invite?token={token}"
    return invitation, link


def accept_invite(db: Session, data: AcceptInvite) -> User:
    """Turn a single-use invitation token into an active user."""
    invitation = db.execute(
        select(Invitation).where(Invitation.token_hash == _hash_token(data.token)).with_for_update()
    ).scalar_one_or_none()

    now = _now()
    if (
        invitation is None
        or invitation.accepted_at is not None
        or invitation.expires_at <= now
    ):
        raise AppError(INVALID_INVITATION)

    if db.scalar(select(User.id).where(User.email == invitation.email)):
        raise AppError(INVALID_INVITATION)

    user = User(
        email=invitation.email,
        full_name=data.full_name,
        password_hash=hash_password(data.password),
        role=invitation.role,
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:  # another accept won the race on the same email
        db.rollback()
        raise AppError(INVALID_INVITATION) from None

    invitation.accepted_at = now
    audit.record(
        db,
        entity="users",
        entity_id=user.id,
        action="invitation_accepted",
        actor_id=user.id,
        snapshot={"email": user.email, "role": user.role},
    )
    db.commit()
    return user


def list_pending_invitations(db: Session) -> list[Invitation]:
    return list(
        db.scalars(
            select(Invitation)
            .where(*_pending_invite_condition())
            .order_by(Invitation.created_at.desc(), Invitation.id.desc())
        )
    )


# ---------------------------------------------------------------------------
# User administration
# ---------------------------------------------------------------------------

def admin_out(user: User) -> UserAdminOut:
    return UserAdminOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        can_finance_ops=user.can_finance_ops,
        permissions=sorted(permissions_for(user)),
        created_at=user.created_at,
    )


def _open_request_refs(db: Session, user: User) -> list[str]:
    """Refs of non-terminal requests the user is the requestor **or** the approver of."""
    return list(
        db.scalars(
            select(FundRequest.ref_no)
            .where(
                or_(FundRequest.requestor_id == user.id, FundRequest.approver_id == user.id),
                FundRequest.status.not_in(TERMINAL_STATUSES),
            )
            .order_by(FundRequest.ref_no)
        )
    )


def _approver_refs_in(db: Session, user: User, statuses: list[str]) -> list[str]:
    return list(
        db.scalars(
            select(FundRequest.ref_no)
            .where(FundRequest.approver_id == user.id, FundRequest.status.in_(statuses))
            .order_by(FundRequest.ref_no)
        )
    )


def update_user(db: Session, actor: User, user_id: int, data: UserUpdate) -> UpdateUserResponse:
    """Role / active / finance-ops changes, with the guard rails from the phase 2 rules table."""
    target = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if target is None:
        raise NotFound("User not found")

    changes = data.model_dump(exclude_unset=True)
    new_role = Role(changes["role"]) if "role" in changes else Role(target.role)
    role_changed = new_role != Role(target.role)
    wants_deactivate = changes.get("is_active") is False

    if target.id == actor.id and (role_changed or wants_deactivate):
        raise BusinessRuleViolation("You cannot change your own role or deactivate yourself")

    leaves_no_super_admin = (
        Role(target.role) == Role.SUPER_ADMIN
        and target.is_active
        and ((role_changed and new_role != Role.SUPER_ADMIN) or wants_deactivate)
    )
    if leaves_no_super_admin:
        others = db.scalar(
            select(func.count(User.id)).where(
                User.role == Role.SUPER_ADMIN.value,
                User.is_active.is_(True),
                User.id != target.id,
            )
        )
        if not others:
            raise BusinessRuleViolation("At least one active Super Admin is required")

    if changes.get("can_finance_ops") is True and new_role != Role.SUPER_ADMIN:
        raise BusinessRuleViolation("can_finance_ops only applies to Super Admin")

    if role_changed:
        refs = _open_request_refs(db, target)
        if refs:
            raise BusinessRuleViolation(
                "User holds a non-terminal request; their role cannot be changed",
                details={"request_refs": refs},
            )

    needs_reassignment: list[str] = []
    if wants_deactivate and Role(target.role) == Role.APPROVER:
        # Edge case #17: only that approver may review a Stage 5 request.
        stuck = _approver_refs_in(db, target, [RequestStatus.PENDING_EXPENSE_APPROVAL.value])
        if stuck:
            raise BusinessRuleViolation(
                "Approver has requests awaiting expense review and cannot be deactivated",
                details={"request_refs": stuck},
            )
        # PENDING_APPROVAL is allowed: Finance can reassign later (Phase 3).
        needs_reassignment = _approver_refs_in(db, target, [RequestStatus.PENDING_APPROVAL.value])

    before = audit.snapshot_of(target, _USER_SNAPSHOT_FIELDS)
    if "role" in changes:
        target.role = new_role.value
        if new_role != Role.SUPER_ADMIN:
            target.can_finance_ops = False  # the flag only means something for Super Admin
    if "is_active" in changes:
        target.is_active = bool(changes["is_active"])
    if "can_finance_ops" in changes:
        target.can_finance_ops = bool(changes["can_finance_ops"])
    after = audit.snapshot_of(target, _USER_SNAPSHOT_FIELDS)

    if after != before:
        audit.record(
            db,
            entity="users",
            entity_id=target.id,
            action="updated",
            actor_id=actor.id,
            snapshot={"before": before, "after": after},
        )

    db.commit()
    return UpdateUserResponse(user=admin_out(target), needs_reassignment=needs_reassignment)


def display_names(db: Session, user_ids: Iterable[int | None]) -> dict[int, str]:
    """Full names for the given ids in one query (for audit and ledger rows)."""
    ids = {uid for uid in user_ids if uid is not None}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.full_name).where(User.id.in_(ids))).tuples().all())


def list_approvers(db: Session, caller: User) -> list[User]:
    """Active approvers, excluding the caller — you cannot assign a request to yourself."""
    return list(
        db.scalars(
            select(User)
            .where(
                User.role == Role.APPROVER.value,
                User.is_active.is_(True),
                User.id != caller.id,
            )
            .order_by(User.full_name, User.id)
        )
    )


def list_requestors(db: Session) -> list[User]:
    """Active requestors — the candidate list for the project members picker."""
    return list(
        db.scalars(
            select(User)
            .where(User.role == Role.REQUESTOR.value, User.is_active.is_(True))
            .order_by(User.full_name, User.id)
        )
    )
