"""Finance use cases for stage 3: disburse, and return a request to the requestor.

Same shape as ``fund_requests/service.py``: lock project then request, money only through
``budget``, status only through ``workflow.apply``, exactly one ``db.commit()``.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import BusinessRuleViolation, Conflict
from app.core.money import ZERO, q2
from app.modules.accounts.models import User
from app.modules.audit import service as audit
from app.modules.finance import budget
from app.modules.finance.models import Disbursement
from app.modules.fund_requests import policies, workflow
from app.modules.fund_requests.models import FundRequest
from app.modules.fund_requests.schemas import DisburseBody, ReasonBody
from app.modules.fund_requests.service import disbursed_total, inr, lock_visible
from app.modules.fund_requests.workflow import Action
from app.modules.projects.models import Project

TXN_REF_USED = "This transaction reference has already been used"
KEY_USED_ELSEWHERE = "This Idempotency-Key was already used for another request"
FUTURE_DATE = "The disbursement date cannot be in the future"


class DuplicateTxnRef(Conflict):
    """409 with its own code, so the UI can point at the txn ref field instead of asking for a reload."""

    code = "duplicate_txn_ref"


def _by_key(db: Session, key: str) -> Disbursement | None:
    return db.scalar(select(Disbursement).where(Disbursement.idempotency_key == key))


def _replay(request: FundRequest, existing: Disbursement | None) -> bool:
    """True when *existing* is this request's earlier disbursement with the same key."""
    if existing is None:
        return False
    if existing.request_id != request.id:
        raise Conflict(KEY_USED_ELSEWHERE)
    return True


def disburse(
    db: Session, actor: User, request_id: int, data: DisburseBody, idempotency_key: str
) -> FundRequest:
    """Pay out ``amount_approved − disbursed_total``.  The same key again returns the request unchanged."""
    request = policies.get_visible_request_or_404(db, actor, request_id)
    if _replay(request, _by_key(db, idempotency_key)):  # fast path, no lock
        return request

    with db.begin_nested():
        project, request = lock_visible(db, actor, request_id)
        # Re-check under the lock: a concurrent call with the same key may have just committed.
        if not _replay(request, _by_key(db, idempotency_key)):
            _pay(db, actor, project, request, data, idempotency_key)
    db.commit()  # on a replay this only ends the transaction and releases the locks
    return request


def _pay(
    db: Session, actor: User, project: Project, request: FundRequest, data: DisburseBody, key: str
) -> None:
    workflow.ensure_version(request, data.version)
    workflow.check(request, Action.DISBURSE, data.version)
    if data.disbursed_on > date.today():
        raise BusinessRuleViolation(FUTURE_DATE)
    amount = q2(q2(request.amount_approved or ZERO) - disbursed_total(db, request.id))
    if amount <= ZERO:
        raise Conflict("Nothing is left to disburse on this request")
    if db.scalar(select(Disbursement.id).where(Disbursement.txn_ref == data.txn_ref)):
        raise DuplicateTxnRef(TXN_REF_USED)

    row = Disbursement(
        request_id=request.id,
        amount=amount,
        txn_ref=data.txn_ref,
        disbursed_on=data.disbursed_on,
        notes=data.notes,
        idempotency_key=key,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:  # lost a race on txn_ref, or the key was used on another project
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""
        if "idempotency" in constraint:
            raise Conflict(KEY_USED_ELSEWHERE) from None
        raise DuplicateTxnRef(TXN_REF_USED) from None

    budget.disburse(db, project, request, amount, actor_id=actor.id)
    workflow.apply(
        db,
        request,
        Action.DISBURSE,
        actor_id=actor.id,
        expected_version=data.version,
        snapshot={
            "disbursement_id": row.id,
            "amount": str(amount),
            "txn_ref": row.txn_ref,
            "disbursed_on": row.disbursed_on.isoformat(),
        },
    )
    audit.notify(
        db,
        user_ids=[request.requestor_id],
        type="request_disbursed",
        message=f"{inr(amount)} was disbursed for {request.ref_no} (txn {row.txn_ref})",
        request_id=request.id,
    )


def return_to_requestor(db: Session, actor: User, request_id: int, data: ReasonBody) -> FundRequest:
    """Send the request back to Stage 1.  A payout already made is kept and stays outstanding (D3)."""
    with db.begin_nested():
        project, request = lock_visible(db, actor, request_id)
        workflow.ensure_version(request, data.version)
        workflow.check(request, Action.RETURN, data.version)

        # PENDING_DISBURSEMENT: frees the reservation.  PENDING_EXPENSE_SUBMISSION: nothing is
        # reserved any more (it was all disbursed), so this is a no-op and the money stays outstanding.
        budget.release(db, project, request, actor_id=actor.id)
        workflow.apply(
            db,
            request,
            Action.RETURN,
            actor_id=actor.id,
            expected_version=data.version,
            reason=data.reason,
            snapshot={"disbursed_total": str(disbursed_total(db, request.id))},
        )
        audit.notify(
            db,
            user_ids=[request.requestor_id],
            type="request_returned",
            message=f"{request.ref_no} was returned to you by {actor.full_name}: {data.reason}",
            request_id=request.id,
        )
    db.commit()
    return request
