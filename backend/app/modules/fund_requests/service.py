"""Request use cases for stages 1–2 (raise, resubmit, withdraw, approve, reject, reassign).

Each write:
  1. finds the request (404 if invisible), locks the **project, then the request** (arch §11.2 #1),
  2. checks the version (409), the object rule (403), the transition (409), the business rules (422),
  3. changes money only through ``finance/budget.py`` and the status only through ``workflow.apply``,
  4. writes notifications and ends with exactly **one** ``db.commit()``.

The work runs inside ``db.begin_nested()`` so a rule that fails half-way leaves nothing behind.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.enums import ProjectStatus, RequestStatus, Role
from app.core.errors import BusinessRuleViolation
from app.core.money import ZERO, q2
from app.modules.accounts.models import User
from app.modules.accounts.service import display_names
from app.modules.audit import service as audit
from app.modules.audit.models import AuditLog
from app.modules.finance import budget
from app.modules.finance.models import Disbursement
from app.modules.fund_requests import policies, workflow
from app.modules.fund_requests.models import FundRequest
from app.modules.fund_requests.schemas import (
    DisbursementOut,
    HistoryEntry,
    ProjectRef,
    ReasonBody,
    ReassignBody,
    RequestCreate,
    RequestDetail,
    RequestResubmit,
    RequestSummary,
    UserRef,
    VersionBody,
)
from app.modules.fund_requests.workflow import Action
from app.modules.projects.models import Project, ProjectMember

NOT_ELIGIBLE = "You can only raise requests on active projects you are a member of"
INVALID_APPROVER = "Approver must be an active approver other than the requestor"
PROJECT_INACTIVE = "This project is inactive; the request cannot be resubmitted"
WITHDRAW_AFTER_PAYOUT = "A request that has received a disbursement cannot be withdrawn"


# ---------------------------------------------------------------------------
# Shared helpers (also used by finance/service.py)
# ---------------------------------------------------------------------------

def inr(amount: Decimal) -> str:
    return f"₹{q2(amount):,.2f}"


def disbursed_total(db: Session, request_id: int) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(Disbursement.amount), ZERO)).where(
            Disbursement.request_id == request_id
        )
    )
    return q2(total)


def lock_visible(db: Session, actor: User, request_id: int) -> tuple[Project, FundRequest]:
    """404 if invisible, then lock the project and the request in that order (fresh values)."""
    request = policies.get_visible_request_or_404(db, actor, request_id)
    project = budget.lock_project(db, request.project_id)
    return project, policies.lock_request(db, request_id)


def full_name(db: Session, user_id: int) -> str:
    return display_names(db, [user_id]).get(user_id, "Unknown user")


def _validate_approver(db: Session, approver_id: int, requestor_id: int) -> User:
    approver = db.get(User, approver_id)
    if (
        approver is None
        or approver.id == requestor_id
        or not approver.is_active
        or approver.role != Role.APPROVER.value
    ):
        raise BusinessRuleViolation(INVALID_APPROVER, details={"approver_id": approver_id})
    return approver


def _next_ref_no(db: Session) -> str:
    n = db.scalar(text("SELECT nextval('fund_request_ref_seq')"))
    return f"FR-{date.today().year}-{n:04d}"


def _finance_recipients(db: Session) -> list[int]:
    """Everybody who can disburse: active Finance, and active Super Admins with finance ops."""
    return list(
        db.scalars(
            select(User.id).where(
                User.is_active.is_(True),
                (User.role == Role.FINANCE.value)
                | ((User.role == Role.SUPER_ADMIN.value) & User.can_finance_ops.is_(True)),
            )
        )
    )


# ---------------------------------------------------------------------------
# Stage 1: submit, resubmit, withdraw
# ---------------------------------------------------------------------------

def create(db: Session, actor: User, data: RequestCreate) -> FundRequest:
    with db.begin_nested():
        # A missing project gets the same 422 as an ineligible one: requestors never learn what exists.
        if db.get(Project, data.project_id) is None:
            raise BusinessRuleViolation(NOT_ELIGIBLE)
        project = budget.lock_project(db, data.project_id)
        is_member = db.scalar(
            select(ProjectMember.user_id).where(
                ProjectMember.project_id == project.id, ProjectMember.user_id == actor.id
            )
        )
        if project.status != ProjectStatus.ACTIVE.value or not is_member:
            raise BusinessRuleViolation(NOT_ELIGIBLE)
        approver = _validate_approver(db, data.approver_id, actor.id)

        request = FundRequest(
            ref_no=_next_ref_no(db),
            project_id=project.id,
            requestor_id=actor.id,
            approver_id=approver.id,
            amount_requested=q2(data.amount_requested),
            purpose=data.purpose,
            supporting_info=data.supporting_info,
            status=RequestStatus.PENDING_APPROVAL.value,
            version=1,
        )
        db.add(request)
        db.flush()
        budget.reserve(db, project, request, request.amount_requested, actor_id=actor.id)

        audit.record(
            db,
            entity=workflow.ENTITY,
            entity_id=request.id,
            action=workflow.AUDIT_ACTION[Action.SUBMIT],
            actor_id=actor.id,
            from_status=None,
            to_status=request.status,
            snapshot=audit.snapshot_of(request, workflow.SNAPSHOT_FIELDS),
        )
        audit.notify(
            db,
            user_ids=[approver.id],
            type="request_submitted",
            message=f"{request.ref_no} from {actor.full_name} needs your approval "
            f"({inr(request.amount_requested)})",
            request_id=request.id,
        )
    db.commit()
    return request


def resubmit(db: Session, actor: User, request_id: int, data: RequestResubmit) -> FundRequest:
    with db.begin_nested():
        project, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, data.version)
        workflow.ensure_party(actor, request, Action.RESUBMIT)
        workflow.check(request, Action.RESUBMIT, data.version)
        if project.status != ProjectStatus.ACTIVE.value:  # D11
            raise BusinessRuleViolation(PROJECT_INACTIVE)
        approver = _validate_approver(db, data.approver_id, request.requestor_id)

        before = audit.snapshot_of(request, ["approver_id", "amount_requested", "purpose", "supporting_info"])
        budget.release(db, project, request, actor_id=actor.id)  # a no-op: going back to Stage 1 released it
        request.approver_id = approver.id
        request.amount_requested = q2(data.amount_requested)
        request.purpose = data.purpose
        request.supporting_info = data.supporting_info
        request.amount_approved = None
        # D3: money already paid out is outstanding, so only the shortfall is reserved.
        paid = disbursed_total(db, request.id)
        reservation = budget.reservation_for_submit(request.amount_requested, paid)
        budget.reserve(db, project, request, reservation, actor_id=actor.id)
        workflow.apply(
            db,
            request,
            Action.RESUBMIT,
            actor_id=actor.id,
            expected_version=data.version,
            snapshot={"before": before, "disbursed_total": str(paid)},
        )
        audit.notify(
            db,
            user_ids=[approver.id],
            type="request_submitted",
            message=f"{request.ref_no} was resubmitted by {actor.full_name} and needs your approval "
            f"({inr(request.amount_requested)})",
            request_id=request.id,
        )
    db.commit()
    return request


def withdraw(db: Session, actor: User, request_id: int, data: VersionBody) -> FundRequest:
    with db.begin_nested():
        project, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, data.version)
        workflow.ensure_party(actor, request, Action.WITHDRAW)
        workflow.check(request, Action.WITHDRAW, data.version)
        paid = disbursed_total(db, request.id)
        if paid != ZERO:  # D10
            raise BusinessRuleViolation(WITHDRAW_AFTER_PAYOUT, details={"disbursed_total": str(paid)})

        budget.release(db, project, request, actor_id=actor.id)
        workflow.apply(db, request, Action.WITHDRAW, actor_id=actor.id, expected_version=data.version)
        audit.notify(
            db,
            user_ids=[request.approver_id],
            type="request_withdrawn",
            message=f"{request.ref_no} was withdrawn by {actor.full_name}",
            request_id=request.id,
        )
    db.commit()
    return request


# ---------------------------------------------------------------------------
# Stage 2: approve, reject, reassign
# ---------------------------------------------------------------------------

def approve(db: Session, actor: User, request_id: int, data: VersionBody) -> FundRequest:
    with db.begin_nested():
        _, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, data.version)
        workflow.ensure_party(actor, request, Action.APPROVE)
        workflow.check(request, Action.APPROVE, data.version)

        paid = disbursed_total(db, request.id)
        request.amount_approved = q2(request.amount_requested)
        skip = paid >= q2(request.amount_requested)  # D3: nothing left to pay → straight to Stage 4
        target = RequestStatus.PENDING_EXPENSE_SUBMISSION if skip else RequestStatus.PENDING_DISBURSEMENT
        workflow.apply(
            db,
            request,
            Action.APPROVE,
            target,
            actor_id=actor.id,
            expected_version=data.version,
            snapshot={"disbursed_total": str(paid)},
        )
        if skip:
            audit.record(
                db,
                entity=workflow.ENTITY,
                entity_id=request.id,
                action="disbursement_skipped",
                actor_id=None,  # system action
                reason="The approved amount has already been disbursed",
                snapshot={"amount_approved": str(request.amount_approved), "disbursed_total": str(paid)},
            )

        ref = request.ref_no
        requestor_message = f"{ref} was approved by {actor.full_name}"
        if skip:
            requestor_message += "; it is already fully disbursed, so expenses can be submitted"
        audit.notify(
            db, user_ids=[request.requestor_id], type="request_approved", message=requestor_message,
            request_id=request.id,
        )
        if not skip:
            audit.notify(
                db,
                user_ids=_finance_recipients(db),
                type="request_approved",
                message=f"{ref} was approved by {actor.full_name} and is awaiting disbursement "
                f"({inr(request.amount_approved - paid)})",
                request_id=request.id,
            )
    db.commit()
    return request


def reject(db: Session, actor: User, request_id: int, data: ReasonBody) -> FundRequest:
    with db.begin_nested():
        project, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, data.version)
        workflow.ensure_party(actor, request, Action.REJECT)
        workflow.check(request, Action.REJECT, data.version)

        budget.release(db, project, request, actor_id=actor.id)
        workflow.apply(
            db, request, Action.REJECT, actor_id=actor.id, expected_version=data.version, reason=data.reason
        )
        audit.notify(
            db,
            user_ids=[request.requestor_id],
            type="request_rejected",
            message=f"{request.ref_no} was rejected by {actor.full_name}: {data.reason}",
            request_id=request.id,
        )
    db.commit()
    return request


def reassign(db: Session, actor: User, request_id: int, data: ReassignBody) -> FundRequest:
    with db.begin_nested():
        _, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, data.version)
        workflow.check(request, Action.REASSIGN, data.version)  # only while PENDING_APPROVAL (D2)
        old_id = request.approver_id
        if data.approver_id == old_id:
            raise BusinessRuleViolation(
                "The request is already assigned to this approver", details={"approver_id": old_id}
            )
        new = _validate_approver(db, data.approver_id, request.requestor_id)
        old_name = full_name(db, old_id)

        request.approver_id = new.id
        workflow.apply(
            db,
            request,
            Action.REASSIGN,
            actor_id=actor.id,
            expected_version=data.version,
            snapshot={
                "old_approver_id": old_id,
                "old_approver_name": old_name,
                "new_approver_id": new.id,
                "new_approver_name": new.full_name,
            },
        )
        requestor_name = full_name(db, request.requestor_id)
        audit.notify(
            db,
            user_ids=[new.id],
            type="request_reassigned",
            message=f"{request.ref_no} from {requestor_name} was reassigned to you for approval "
            f"({inr(request.amount_requested)})",
            request_id=request.id,
        )
        audit.notify(
            db,
            user_ids=[old_id],
            type="request_reassigned",
            message=f"{request.ref_no} was reassigned from you to {new.full_name}",
            request_id=request.id,
        )
    db.commit()
    return request


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _user_refs(db: Session, ids: set[int]) -> dict[int, UserRef]:
    if not ids:
        return {}
    return {u.id: UserRef.model_validate(u) for u in db.scalars(select(User).where(User.id.in_(ids)))}


def _project_refs(db: Session, ids: set[int]) -> dict[int, ProjectRef]:
    if not ids:
        return {}
    rows = db.scalars(select(Project).where(Project.id.in_(ids)))
    return {p.id: ProjectRef.model_validate(p) for p in rows}


def _summary_fields(request: FundRequest, users: dict[int, UserRef], projects: dict[int, ProjectRef]) -> dict:
    return {
        "id": request.id,
        "ref_no": request.ref_no,
        "status": request.status,
        "project": projects[request.project_id],
        "requestor": users[request.requestor_id],
        "approver": users[request.approver_id],
        "amount_requested": request.amount_requested,
        "amount_approved": request.amount_approved,
        "created_at": request.created_at,
        "updated_at": request.updated_at,
    }


def to_summaries(db: Session, rows: list[FundRequest]) -> list[RequestSummary]:
    """List rows with their project and people, in two queries for the whole page."""
    users = _user_refs(db, {r.requestor_id for r in rows} | {r.approver_id for r in rows})
    projects = _project_refs(db, {r.project_id for r in rows})
    return [RequestSummary(**_summary_fields(r, users, projects)) for r in rows]


def to_detail(db: Session, user: User, request: FundRequest) -> RequestDetail:
    users = _user_refs(db, {request.requestor_id, request.approver_id})
    projects = _project_refs(db, {request.project_id})
    rows = list(
        db.scalars(
            select(Disbursement).where(Disbursement.request_id == request.id).order_by(Disbursement.id)
        )
    )
    names = display_names(db, (d.created_by for d in rows))
    paid = q2(sum((q2(d.amount) for d in rows), ZERO))
    disbursable = ZERO
    if request.status == RequestStatus.PENDING_DISBURSEMENT.value and request.amount_approved is not None:
        disbursable = q2(max(q2(request.amount_approved) - paid, ZERO))
    return RequestDetail(
        **_summary_fields(request, users, projects),
        purpose=request.purpose,
        supporting_info=request.supporting_info,
        reserved_amount=request.reserved_amount,
        disbursed_total=paid,
        disbursable_amount=disbursable,
        version=request.version,
        disbursements=[
            DisbursementOut(
                id=d.id,
                amount=d.amount,
                txn_ref=d.txn_ref,
                disbursed_on=d.disbursed_on,
                notes=d.notes,
                created_by_name=names.get(d.created_by),
                created_at=d.created_at,
            )
            for d in rows
        ],
        allowed_actions=workflow.allowed_actions(user, request, paid),
    )


def get_detail(db: Session, user: User, request_id: int) -> RequestDetail:
    return to_detail(db, user, policies.get_visible_request_or_404(db, user, request_id))


def history(db: Session, user: User, request_id: int) -> list[HistoryEntry]:
    """Audit rows of the request, oldest first; same visibility as the request itself."""
    request = policies.get_visible_request_or_404(db, user, request_id)
    rows = list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.entity == workflow.ENTITY, AuditLog.entity_id == request.id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    )
    names = display_names(db, (r.actor_id for r in rows))
    return [
        HistoryEntry(
            id=r.id,
            action=r.action,
            from_status=r.from_status,
            to_status=r.to_status,
            reason=r.reason,
            actor_name=names.get(r.actor_id),
            snapshot=r.snapshot,
            created_at=r.created_at,
        )
        for r in rows
    ]

