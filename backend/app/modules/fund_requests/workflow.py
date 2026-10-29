"""Request state machine for stages 1–3 (arch §7, phase 3 §2).

``TRANSITIONS`` is the single table of what may happen: a ``(status, action)`` pair that is
not in it is a **409**.  ``allowed_actions`` reads the same table, so the UI is never offered
an action the API would refuse.  ``apply`` is the only place a request's status changes.
"""

import enum
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.enums import RequestStatus
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
}


def ensure_party(user: User, request: FundRequest, action: Action) -> None:
    """403 when *user* is not the person this action belongs to (requestor / assigned approver)."""
    rule = _PARTY_RULES.get(action)
    if rule is not None and not rule[0](user, request):
        raise Forbidden(rule[1])


def allowed_actions(user: User, request: FundRequest, disbursed_total: Decimal) -> list[str]:
    """Actions *user* may take on *request* now: status + permission + object rule (phase 3 §3.3)."""
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
        result.append(action.value)
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
) -> AuditLog:
    """Move *request* along the table, bump ``version`` and write the audit row (flushes, no commit).

    *to_status* defaults to the transition's main target; *snapshot* holds extra details merged
    into the request snapshot taken after the change.
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
        action=AUDIT_ACTION[action],
        actor_id=actor_id,
        from_status=from_status,
        to_status=target.value,
        reason=reason,
        snapshot={**audit.snapshot_of(request, SNAPSHOT_FIELDS), **(snapshot or {})},
    )
