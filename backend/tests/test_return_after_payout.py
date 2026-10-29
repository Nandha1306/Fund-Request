"""D3: a request returned after payout keeps its disbursement; only the shortfall is paid later,
and a zero shortfall skips Stage 3."""

from decimal import Decimal as D

from sqlalchemy import func, select

from app.modules.audit.models import AuditLog
from app.modules.finance import budget
from app.modules.finance.models import Disbursement


def _consistent(db, project) -> None:
    db.refresh(project)
    assert budget.reconcile(db, project.id) == budget.buckets_of(project)


def _paid_and_returned(flow, people, amount="30000") -> int:
    rid = flow.raised(amount=amount)["id"]
    flow.ok(flow.act(people.approver, rid, "approve"))
    flow.ok(flow.disburse(rid))
    flow.ok(flow.act(people.finance, rid, "return", reason="Receipts missing"))
    return rid


def test_same_amount_skips_disbursement(db_session, flow, people):
    rid = _paid_and_returned(flow, people)
    resubmitted = flow.ok(flow.resubmit(people.requestor, rid, amount="30000"))
    assert resubmitted["reserved_amount"] == "0.00"  # nothing new to reserve
    _consistent(db_session, flow.project)

    approved = flow.ok(flow.act(people.approver, rid, "approve"))
    assert approved["status"] == "PENDING_EXPENSE_SUBMISSION"
    assert (approved["disbursed_total"], approved["disbursable_amount"]) == ("30000.00", "0.00")
    assert len(approved["disbursements"]) == 1
    assert db_session.scalar(select(func.count(Disbursement.id))) == 1

    actions = list(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.entity_id == rid, AuditLog.entity == "fund_requests")
            .order_by(AuditLog.id)
        )
    )
    assert actions == [
        "submitted", "approved", "disbursed", "returned", "resubmitted", "approved", "disbursement_skipped",
    ]
    skipped = db_session.scalar(
        select(AuditLog).where(AuditLog.entity_id == rid, AuditLog.action == "disbursement_skipped")
    )
    assert skipped.actor_id is None  # system action

    # Finance is not told to pay anything the second time.
    approvals = [n for n in flow.ok(flow.client.get(
        "/api/notifications", headers=flow.auth(people.finance)
    ))["items"] if n["type"] == "request_approved"]
    assert len(approvals) == 1

    assert [e.entry_type for e in budget.ledger_rows(db_session, flow.project.id)] == [
        "ALLOCATE", "RESERVE", "DISBURSE",
    ]
    _consistent(db_session, flow.project)
    assert (flow.project.reserved, flow.project.outstanding) == (D("0.00"), D("30000.00"))


def test_larger_amount_pays_only_the_shortfall(db_session, flow, people):
    rid = _paid_and_returned(flow, people)
    resubmitted = flow.ok(flow.resubmit(people.requestor, rid, amount="40000"))
    assert resubmitted["reserved_amount"] == "10000.00"

    approved = flow.ok(flow.act(people.approver, rid, "approve"))
    assert approved["status"] == "PENDING_DISBURSEMENT" and approved["disbursable_amount"] == "10000.00"

    paid = flow.ok(flow.disburse(rid))
    assert paid["status"] == "PENDING_EXPENSE_SUBMISSION"
    assert [d["amount"] for d in paid["disbursements"]] == ["30000.00", "10000.00"]
    assert paid["disbursed_total"] == "40000.00"
    _consistent(db_session, flow.project)
    assert (flow.project.reserved, flow.project.outstanding) == (D("0.00"), D("40000.00"))


def test_smaller_amount_also_skips(db_session, flow, people):
    rid = _paid_and_returned(flow, people)
    flow.ok(flow.resubmit(people.requestor, rid, amount="20000"))
    approved = flow.ok(flow.act(people.approver, rid, "approve"))
    assert approved["status"] == "PENDING_EXPENSE_SUBMISSION"
    _consistent(db_session, flow.project)
