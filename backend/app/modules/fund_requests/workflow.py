"""Request state machine for stages 1–6 (arch §7.3, phase 3 §2, phase 4 §2).

``TRANSITIONS`` is the single table of what may happen: a ``(status, action)`` pair that is
not in it is a **409**.  ``allowed_actions`` reads the same table, so the UI is never offered
an action the API would refuse.  ``apply`` is the only place a request's status changes.

Stage 4–6 actions also depend on the expense items (``item_guard``).  Item-level actions
(``save_expenses``, ``review_item``, ``verify_item``) keep the status but still check and bump the
version and write an audit row, so a stale screen gets a 409.
"""

import enum
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.enums import ApprovalStatus, RequestStatus, VerificationStatus
from app.core.errors import Conflict, Forbidden
from app.core.money import ZERO, q2
from app.modules.accounts.models import User
from app.modules.accounts.permissions import permissions_for
from app.modules.audit import service as audit
from app.modules.audit.models import AuditLog
from app.modules.fund_requests.models import FundRequest

ENTITY = "fund_requests"
STALE_VERSION = "This request was changed by someone else. Reload and try again."

SNAPSHOT_FIELDS = [
    "ref_no",
    "status",
    "project_id",
    "requestor_id",
    "approver_id",
    "amount_requested",
    "amount_approved",
    "reserved_amount",
    "version",
]


class Action(enum.StrEnum):
    # Declaration order is the order of ``allowed_actions``.
    SUBMIT = "submit"
    RESUBMIT = "resubmit"
    WITHDRAW = "withdraw"
    APPROVE = "approve"
    REJECT = "reject"
    REASSIGN = "reassign"
    DISBURSE = "disburse"
    RETURN = "return"
    # Stages 4–6 (phase 4 §2)
    SAVE_EXPENSES = "save_expenses"
    SUBMIT_EXPENSES = "submit_expenses"
    REVIEW_ITEM = "review_item"
    SEND_BACK_EXPENSES = "send_back_expenses"
    APPROVE_EXPENSES = "approve_expenses"
    VERIFY_ITEM = "verify_item"
    RETURN_EXPENSES = "return_expenses"
    LIQUIDATE = "liquidate"


# Audit ``action`` written for each workflow action.
AUDIT_ACTION: dict[Action, str] = {
    Action.SUBMIT: "submitted",
    Action.RESUBMIT: "resubmitted",
    Action.WITHDRAW: "withdrawn",
    Action.APPROVE: "approved",
    Action.REJECT: "rejected",
    Action.REASSIGN: "reassigned",
    Action.DISBURSE: "disbursed",
    Action.RETURN: "returned",
    Action.SAVE_EXPENSES: "expenses_saved",
    Action.SUBMIT_EXPENSES: "expenses_submitted",
    Action.REVIEW_ITEM: "item_reviewed",  # the service passes item_approved / item_rejected
    Action.SEND_BACK_EXPENSES: "expenses_sent_back",
    Action.APPROVE_EXPENSES: "expenses_approved",
    Action.VERIFY_ITEM: "item_checked",  # the service passes item_verified / item_returned
    Action.RETURN_EXPENSES: "expenses_returned",
    Action.LIQUIDATE: "liquidated",
}

# Name shown in ``allowed_actions`` where it differs from the action itself (phase 4 §3.3).
UI_ACTION: dict[Action, str] = {
    Action.SAVE_EXPENSES: "edit_expenses",
    Action.REVIEW_ITEM: "review_items",
    Action.VERIFY_ITEM: "verify_items",
}


@dataclass(frozen=True)
class Transition:
    to: RequestStatus
    permission: str
    # Other legal targets, e.g. approve skipping Stage 3 when everything is already paid (D3).
    alternatives: tuple[RequestStatus, ...] = ()

    @property
    def targets(self) -> tuple[RequestStatus, ...]:
        return (self.to, *self.alternatives)


_S = RequestStatus

TRANSITIONS: dict[tuple[RequestStatus | None, Action], Transition] = {
    (None, Action.SUBMIT): Transition(_S.PENDING_APPROVAL, "request.create"),
    (_S.RETURNED_FOR_CORRECTION, Action.RESUBMIT): Transition(_S.PENDING_APPROVAL, "request.resubmit"),
    (_S.RETURNED_FOR_CORRECTION, Action.WITHDRAW): Transition(_S.WITHDRAWN, "request.withdraw"),
    (_S.PENDING_APPROVAL, Action.APPROVE): Transition(
        _S.PENDING_DISBURSEMENT, "request.approve", (_S.PENDING_EXPENSE_SUBMISSION,)
    ),
    (_S.PENDING_APPROVAL, Action.REJECT): Transition(_S.RETURNED_FOR_CORRECTION, "request.approve"),
    (_S.PENDING_APPROVAL, Action.REASSIGN): Transition(_S.PENDING_APPROVAL, "request.reassign"),
    (_S.PENDING_DISBURSEMENT, Action.DISBURSE): Transition(_S.PENDING_EXPENSE_SUBMISSION, "request.disburse"),
    (_S.PENDING_DISBURSEMENT, Action.RETURN): Transition(_S.RETURNED_FOR_CORRECTION, "request.return"),
    (_S.PENDING_EXPENSE_SUBMISSION, Action.RETURN): Transition(_S.RETURNED_FOR_CORRECTION, "request.return"),
    # Stage 4: expenses
    (_S.PENDING_EXPENSE_SUBMISSION, Action.SAVE_EXPENSES): Transition(
        _S.PENDING_EXPENSE_SUBMISSION, "expense.submit"
    ),
    (_S.EXPENSE_RETURNED, Action.SAVE_EXPENSES): Transition(_S.EXPENSE_RETURNED, "expense.submit"),
    (_S.PENDING_EXPENSE_SUBMISSION, Action.SUBMIT_EXPENSES): Transition(
        _S.PENDING_EXPENSE_APPROVAL, "expense.submit"
    ),
    (_S.EXPENSE_RETURNED, Action.SUBMIT_EXPENSES): Transition(_S.PENDING_EXPENSE_APPROVAL, "expense.submit"),
    # Stage 5: item review
    (_S.PENDING_EXPENSE_APPROVAL, Action.REVIEW_ITEM): Transition(
        _S.PENDING_EXPENSE_APPROVAL, "expense.review"
    ),
    (_S.PENDING_EXPENSE_APPROVAL, Action.SEND_BACK_EXPENSES): Transition(
        _S.EXPENSE_RETURNED, "expense.review"
    ),
    (_S.PENDING_EXPENSE_APPROVAL, Action.APPROVE_EXPENSES): Transition(
        _S.PENDING_VERIFICATION, "expense.review"
    ),
    # Stage 6: verification and liquidation
    (_S.PENDING_VERIFICATION, Action.VERIFY_ITEM): Transition(_S.PENDING_VERIFICATION, "expense.verify"),
    (_S.PENDING_VERIFICATION, Action.RETURN_EXPENSES): Transition(_S.EXPENSE_RETURNED, "expense.verify"),
    (_S.PENDING_VERIFICATION, Action.LIQUIDATE): Transition(_S.LIQUIDATED, "request.liquidate"),
}


# ---------------------------------------------------------------------------
# Object rules: who, beyond the permission, may act on *this* request
# ---------------------------------------------------------------------------

def _is_requestor(user: User, request: FundRequest) -> bool:
    return request.requestor_id == user.id


def _is_approver(user: User, request: FundRequest) -> bool:
    return request.approver_id == user.id


_PARTY_RULES: dict[Action, tuple[Callable[[User, FundRequest], bool], str]] = {
    Action.RESUBMIT: (_is_requestor, "Only the requestor can resubmit this request"),
    Action.WITHDRAW: (_is_requestor, "Only the requestor can withdraw this request"),
    Action.APPROVE: (_is_approver, "Only the assigned approver can approve this request"),
    Action.REJECT: (_is_approver, "Only the assigned approver can reject this request"),
    Action.SAVE_EXPENSES: (_is_requestor, "Only the requestor can edit the expenses of this request"),
    Action.SUBMIT_EXPENSES: (_is_requestor, "Only the requestor can submit the expenses of this request"),
    Action.REVIEW_ITEM: (_is_approver, "Only the assigned approver can review these expenses"),
    Action.SEND_BACK_EXPENSES: (_is_approver, "Only the assigned approver can send these expenses back"),
    Action.APPROVE_EXPENSES: (_is_approver, "Only the assigned approver can approve these expenses"),
}


# ---------------------------------------------------------------------------
# Item rules: what the expense items must look like for a stage 4–6 action
# ---------------------------------------------------------------------------

NO_ITEMS = "Add at least one expense item before submitting"
NOTHING_TO_REVIEW = "Every expense item is already approved"
REVIEW_PENDING = "Review every expense item before sending the expenses back"
NOTHING_REJECTED = "Reject at least one expense item to send the expenses back"
NOT_ALL_APPROVED = "Every expense item must be approved before final approval"
NOTHING_TO_VERIFY = "Every expense item is already verified"
VERIFY_PENDING = "Verify or return every expense item before returning the expenses"
NOTHING_RETURNED = "Return at least one expense item to send the expenses back"
NOT_ALL_VERIFIED = "Every expense item must be verified before liquidation"


def is_item_editable(item: Any) -> bool:
    """D9: approval PENDING or REJECTED, or returned by Finance.  An approved item is locked."""
    return (
        item.approval_status in (ApprovalStatus.PENDING, ApprovalStatus.REJECTED)
        or item.verification_status == VerificationStatus.RETURNED
    )


def item_guard(action: Action, items: Sequence[Any]) -> str | None:
    """Why the expense items do not allow *action* (a 422 message), or ``None`` when they do.

    The same function feeds ``allowed_actions`` and the services, so a button is only offered
    when the API would accept it.
    """
    approval = [i.approval_status for i in items]
    verification = [i.verification_status for i in items]
    match action:
        case Action.SUBMIT_EXPENSES:
            return None if items else NO_ITEMS
        case Action.REVIEW_ITEM:
            return None if any(a != ApprovalStatus.APPROVED for a in approval) else NOTHING_TO_REVIEW
        case Action.SEND_BACK_EXPENSES:
            if ApprovalStatus.PENDING in approval:
                return REVIEW_PENDING
            return None if ApprovalStatus.REJECTED in approval else NOTHING_REJECTED
        case Action.APPROVE_EXPENSES:
            ok = bool(items) and all(a == ApprovalStatus.APPROVED for a in approval)
            return None if ok else NOT_ALL_APPROVED
        case Action.VERIFY_ITEM:
            return None if any(v != VerificationStatus.VERIFIED for v in verification) else NOTHING_TO_VERIFY
        case Action.RETURN_EXPENSES:
            if VerificationStatus.PENDING in verification:
                return VERIFY_PENDING
            return None if VerificationStatus.RETURNED in verification else NOTHING_RETURNED
        case Action.LIQUIDATE:
            ok = bool(items) and all(v == VerificationStatus.VERIFIED for v in verification)
            return None if ok else NOT_ALL_VERIFIED
    return None


def ensure_party(user: User, request: FundRequest, action: Action) -> None:
    """403 when *user* is not the person this action belongs to (requestor / assigned approver)."""
    rule = _PARTY_RULES.get(action)
    if rule is not None and not rule[0](user, request):
        raise Forbidden(rule[1])


def allowed_actions(
    user: User, request: FundRequest, disbursed_total: Decimal, items: Sequence[Any] = ()
) -> list[str]:
    """Actions *user* may take on *request* now: status + permission + object rule + item rule
    (phase 3 §3.3, phase 4 §3.3).  *items* are the request's expense items."""
    status = RequestStatus(request.status)
    perms = permissions_for(user)
    result: list[str] = []
    for action in Action:
        transition = TRANSITIONS.get((status, action))
        if transition is None or transition.permission not in perms:
            continue
        rule = _PARTY_RULES.get(action)
        if rule is not None and not rule[0](user, request):
            continue
        if action == Action.WITHDRAW and q2(disbursed_total) != ZERO:
            continue  # D10: nothing may have been paid out
        if item_guard(action, items) is not None:
            continue
        result.append(UI_ACTION.get(action, action.value))
    return result


# ---------------------------------------------------------------------------
# Checks and the single status writer
# ---------------------------------------------------------------------------

def check(request: FundRequest, action: Action, expected_version: int) -> Transition:
    """409 unless *action* is legal from the current status and the caller saw the current version."""
    status = RequestStatus(request.status)
    transition = TRANSITIONS.get((status, action))
    if transition is None:
        raise Conflict(
            f"Cannot {action.value} a request in status {status.value}",
            details={"status": status.value, "action": action.value},
        )
    ensure_version(request, expected_version)
    return transition


def ensure_version(request: FundRequest, expected_version: int) -> None:
    if request.version != expected_version:
        raise Conflict(STALE_VERSION, details={"current_version": request.version})


def apply(
    db: Session,
    request: FundRequest,
    action: Action,
    to_status: RequestStatus | None = None,
    *,
    actor_id: int | None,
    expected_version: int,
    reason: str | None = None,
    snapshot: dict[str, Any] | None = None,
    audit_action: str | None = None,
) -> AuditLog:
    """Move *request* along the table, bump ``version`` and write the audit row (flushes, no commit).

    *to_status* defaults to the transition's main target; *snapshot* holds extra details merged
    into the request snapshot taken after the change; *audit_action* overrides the audit name
    (item decisions: ``item_approved`` / ``item_rejected`` / ``item_verified`` / ``item_returned``).
    """
    transition = check(request, action, expected_version)
    target = to_status or transition.to
    if target not in transition.targets:
        raise ValueError(f"{action.value} cannot lead to {target.value}")

    from_status = request.status
    request.status = target.value
    request.version += 1
    return audit.record(
        db,
        entity=ENTITY,
        entity_id=request.id,
        action=audit_action or AUDIT_ACTION[action],
        actor_id=actor_id,
        from_status=from_status,
        to_status=target.value,
        reason=reason,
        snapshot={**audit.snapshot_of(request, SNAPSHOT_FIELDS), **(snapshot or {})},
    )
