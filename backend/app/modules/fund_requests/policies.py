"""Request visibility rules (D12).

Invisible request → **404**; visible but not allowed → 403.  Finance and Super Admin
(``request.view_all``) see every request; everybody else sees only the requests they raised
or are the assigned approver of.
"""

from sqlalchemy import and_, or_, select, true
from sqlalchemy.orm import Session

from app.core.enums import RequestStatus
from app.core.errors import NotFound
from app.modules.accounts.models import User
from app.modules.accounts.permissions import permissions_for
from app.modules.fund_requests.models import FundRequest

_S = RequestStatus

# "Waiting on me" (phase 3 §3.4).  Stage 4–6 statuses are listed already so the queue keeps working later.
REQUESTOR_TURN = (_S.RETURNED_FOR_CORRECTION, _S.PENDING_EXPENSE_SUBMISSION, _S.EXPENSE_RETURNED)
APPROVER_TURN = (_S.PENDING_APPROVAL, _S.PENDING_EXPENSE_APPROVAL)
FINANCE_TURN = (_S.PENDING_DISBURSEMENT, _S.PENDING_VERIFICATION)


def _values(statuses: tuple[RequestStatus, ...]) -> list[str]:
    return [s.value for s in statuses]


def visible_requests_filter(user: User):
    """SQL condition limiting ``select(FundRequest)`` to what *user* may see."""
    if "request.view_all" in permissions_for(user):
        return true()
    return or_(FundRequest.requestor_id == user.id, FundRequest.approver_id == user.id)


def waiting_on_filter(user: User):
    """Requests whose next step belongs to *user*: the union of the rows that apply to them."""
    conditions = [
        and_(FundRequest.requestor_id == user.id, FundRequest.status.in_(_values(REQUESTOR_TURN))),
        and_(FundRequest.approver_id == user.id, FundRequest.status.in_(_values(APPROVER_TURN))),
    ]
    if "request.disburse" in permissions_for(user):
        conditions.append(FundRequest.status.in_(_values(FINANCE_TURN)))
    return or_(*conditions)


def get_visible_request_or_404(
    db: Session, user: User, request_id: int, *, lock: bool = False
) -> FundRequest:
    """The request, if *user* may see it.  ``lock=True`` adds ``FOR UPDATE`` and refreshes the object;
    callers that also lock the project must lock it **first** (see ``lock_request``)."""
    stmt = select(FundRequest).where(FundRequest.id == request_id, visible_requests_filter(user))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    request = db.execute(stmt).scalar_one_or_none()
    if request is None:
        raise NotFound("Request not found")
    return request


def lock_request(db: Session, request_id: int) -> FundRequest:
    """``SELECT … FOR UPDATE`` on a request already known to be visible, refreshing stale attributes.

    Visibility is not re-applied here on purpose: if a concurrent reassign moved the request away
    from the caller, they get the 409 "changed by someone else" from the version check instead of a
    confusing 404 halfway through their action.
    """
    request = db.execute(
        select(FundRequest)
        .where(FundRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if request is None:
        raise NotFound("Request not found")
    return request
