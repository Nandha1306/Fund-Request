"""Request endpoints (phase 3 §3.2): raise, list, detail, the stage 1–3 actions, liquidation (phase 4) and
the history.  The stage 4–6 expense endpoints are in ``expenses/router.py``."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query
from sqlalchemy import or_, select

from app.core.db import DbSession
from app.core.enums import RequestStatus
from app.core.pagination import Page, Pagination, paginate
from app.modules.accounts.deps import CurrentUser, require
from app.modules.accounts.models import User
from app.modules.finance import service as finance
from app.modules.fund_requests import policies, service
from app.modules.fund_requests.models import FundRequest
from app.modules.fund_requests.schemas import (
    DisburseBody,
    HistoryEntry,
    LiquidateBody,
    ReasonBody,
    ReassignBody,
    RequestCreate,
    RequestDetail,
    RequestResubmit,
    RequestSummary,
    VersionBody,
)

router = APIRouter(tags=["requests"])

IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


@router.post("/requests", response_model=RequestDetail, status_code=201)
def create_request(
    body: RequestCreate, db: DbSession, actor: Annotated[User, require("request.create")]
) -> RequestDetail:
    """Raise a request: reserves the amount against the project and notifies the approver."""
    return service.to_detail(db, actor, service.create(db, actor, body))


@router.get("/requests", response_model=Page[RequestSummary])
def list_requests(
    db: DbSession,
    user: CurrentUser,
    params: Pagination,
    status: Annotated[list[RequestStatus] | None, Query()] = None,
    project_id: int | None = None,
    requestor_id: int | None = None,
    approver_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
    mine: Literal["action"] | None = None,
) -> Page[RequestSummary]:
    """Requests the caller may see, newest first.  ``mine=action`` keeps only those waiting on them."""
    stmt = select(FundRequest).where(policies.visible_requests_filter(user))
    if status:
        stmt = stmt.where(FundRequest.status.in_([RequestStatus(s).value for s in status]))
    if project_id is not None:
        stmt = stmt.where(FundRequest.project_id == project_id)
    if requestor_id is not None:
        stmt = stmt.where(FundRequest.requestor_id == requestor_id)
    if approver_id is not None:
        stmt = stmt.where(FundRequest.approver_id == approver_id)
    if date_from is not None:
        stmt = stmt.where(FundRequest.created_at >= _start_of(date_from))
    if date_to is not None:  # inclusive: the whole of date_to
        stmt = stmt.where(FundRequest.created_at < _start_of(date_to + timedelta(days=1)))
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(FundRequest.ref_no.ilike(pattern), FundRequest.purpose.ilike(pattern)))
    if mine == "action":
        stmt = stmt.where(policies.waiting_on_filter(user))
    stmt = stmt.order_by(FundRequest.created_at.desc(), FundRequest.id.desc())

    rows, total = paginate(db, stmt, params)
    return Page[RequestSummary](
        items=service.to_summaries(db, rows), total=total, page=params.page, size=params.size
    )


@router.get("/requests/{request_id}", response_model=RequestDetail)
def get_request(request_id: int, db: DbSession, user: CurrentUser) -> RequestDetail:
    return service.get_detail(db, user, request_id)


@router.put("/requests/{request_id}", response_model=RequestDetail)
def resubmit_request(
    request_id: int,
    body: RequestResubmit,
    db: DbSession,
    actor: Annotated[User, require("request.resubmit")],
) -> RequestDetail:
    """Resubmit a returned request (Stage 1 again), possibly with a new amount or approver."""
    return service.to_detail(db, actor, service.resubmit(db, actor, request_id, body))


@router.post("/requests/{request_id}/approve", response_model=RequestDetail)
def approve_request(
    request_id: int, body: VersionBody, db: DbSession, actor: Annotated[User, require("request.approve")]
) -> RequestDetail:
    return service.to_detail(db, actor, service.approve(db, actor, request_id, body))


@router.post("/requests/{request_id}/reject", response_model=RequestDetail)
def reject_request(
    request_id: int, body: ReasonBody, db: DbSession, actor: Annotated[User, require("request.approve")]
) -> RequestDetail:
    return service.to_detail(db, actor, service.reject(db, actor, request_id, body))


@router.post("/requests/{request_id}/reassign", response_model=RequestDetail)
def reassign_request(
    request_id: int, body: ReassignBody, db: DbSession, actor: Annotated[User, require("request.reassign")]
) -> RequestDetail:
    return service.to_detail(db, actor, service.reassign(db, actor, request_id, body))


@router.post("/requests/{request_id}/withdraw", response_model=RequestDetail)
def withdraw_request(
    request_id: int, body: VersionBody, db: DbSession, actor: Annotated[User, require("request.withdraw")]
) -> RequestDetail:
    return service.to_detail(db, actor, service.withdraw(db, actor, request_id, body))


@router.post("/requests/{request_id}/disburse", response_model=RequestDetail)
def disburse_request(
    request_id: int,
    body: DisburseBody,
    db: DbSession,
    actor: Annotated[User, require("request.disburse")],
    idempotency_key: IdempotencyKey,
) -> RequestDetail:
    """Pay out the approved shortfall.  Sending the same ``Idempotency-Key`` again creates nothing new."""
    return service.to_detail(db, actor, finance.disburse(db, actor, request_id, body, idempotency_key))


@router.post("/requests/{request_id}/return", response_model=RequestDetail)
def return_request(
    request_id: int, body: ReasonBody, db: DbSession, actor: Annotated[User, require("request.return")]
) -> RequestDetail:
    return service.to_detail(db, actor, finance.return_to_requestor(db, actor, request_id, body))


@router.post("/requests/{request_id}/liquidate", response_model=RequestDetail)
def liquidate_request(
    request_id: int, body: LiquidateBody, db: DbSession, actor: Annotated[User, require("request.liquidate")]
) -> RequestDetail:
    """Close the request: every item verified; underspend needs ``balance_return_ref``."""
    return service.to_detail(db, actor, finance.liquidate(db, actor, request_id, body))


@router.get("/requests/{request_id}/history", response_model=list[HistoryEntry])
def request_history(request_id: int, db: DbSession, user: CurrentUser) -> list[HistoryEntry]:
    return service.history(db, user, request_id)
