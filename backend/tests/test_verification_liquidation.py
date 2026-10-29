"""Stage 6 (phase 4 §2): item verification, Finance return (items pass Stage 5 again, D9), liquidation in
both settlement branches (arch §9.2), one liquidation per request.  ``reconcile()`` is checked after every
money step."""

from decimal import Decimal as D

from sqlalchemy import func, select

from app.core.enums import Role
from app.modules.audit.models import AuditLog, Notification
from app.modules.finance import budget
from app.modules.finance.models import Liquidation
from app.modules.finance.service import BALANCE_REF_REQUIRED
from tests.conftest import expense_item


def _consistent(db, project) -> budget.Buckets:
    db.refresh(project)
    assert budget.reconcile(db, project.id) == budget.buckets_of(project)
    return budget.buckets_of(project)


def _ledger_tail(db, project, n: int) -> list[tuple[str, D]]:
    return [(r.entry_type, r.amount) for r in budget.ledger_rows(db, project.id)][-n:]


def _last_audit(db, request_id) -> AuditLog:
    return db.scalars(
        select(AuditLog)
        .where(AuditLog.entity == "fund_requests", AuditLog.entity_id == request_id)
        .order_by(AuditLog.id.desc())
    ).first()


def _last_note(db, user) -> tuple[str, str]:
    n = db.scalars(
        select(Notification).where(Notification.user_id == user.id).order_by(Notification.id.desc())
    ).first()
    return n.type, n.message


def _error(resp) -> tuple[int, str]:
    return resp.status_code, resp.json()["error"]["message"]


# ---------------------------------------------------------------------------
# Verify / return guards
# ---------------------------------------------------------------------------


def test_verify_guards(db_session, flow, people, make_user):
    body = flow.approved_all("30000.00", "10000.00", "20000.00")
    rid = body["id"]
    first, second = (i["id"] for i in body["expense_items"])

    resp = flow.verify(rid, first, "RETURNED")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "validation_error"
    assert flow.verify(rid, first, "APPROVED").status_code == 400
    assert flow.verify(rid, first, user=people.approver).status_code == 403
    assert flow.verify(rid, first, user=people.requestor).status_code == 403
    assert flow.verify(rid, first, user=make_user(Role.SUPER_ADMIN)).status_code == 403
    assert flow.verify(rid, 999999).status_code == 404

    version = flow.version(rid)
    body = flow.ok(flow.verify(rid, first, reason="ignored"))
    assert body["version"] == version + 1 and body["status"] == "PENDING_VERIFICATION"
    item = body["expense_items"][0]
    assert (item["verification_status"], item["verification_reason"], item["verified_by_name"]) == (
        "VERIFIED",
        None,
        "Finance Officer",
    )
    assert item["verified_at"] and body["verified_total"] == "10000.00"
    assert _error(flow.verify(rid, first)) == (409, "This expense item is already verified")
    assert flow.verify(rid, second, version=version).status_code == 409

    # A Super Admin with finance ops may verify; a RETURNED item may still be verified afterwards.
    ops = make_user(Role.SUPER_ADMIN, can_finance_ops=True, full_name="Ops Admin")
    body = flow.ok(flow.verify(rid, second, "RETURNED", reason="Illegible", user=ops))
    assert body["expense_items"][1]["verification_reason"] == "Illegible"
    audit = _last_audit(db_session, rid)
    assert (audit.action, audit.reason, audit.snapshot["item_id"], audit.snapshot["amount"]) == (
        "item_returned",
        "Illegible",
        second,
        "20000.00",
    )
    body = flow.ok(flow.verify(rid, second))
    assert body["expense_items"][1]["verification_status"] == "VERIFIED"
    assert _last_audit(db_session, rid).action == "item_verified"
    _consistent(db_session, flow.project)


def test_return_guards_and_allowed_actions(db_session, flow, people):
    body = flow.approved_all("30000.00", "10000.00", "20000.00")
    rid = body["id"]
    first, second = (i["id"] for i in body["expense_items"])
    assert flow.ok(flow.get(people.finance, rid))["allowed_actions"] == ["verify_items"]

    assert _error(flow.return_expenses(rid)) == (
        422,
        "Verify or return every expense item before returning the expenses",
    )
    assert _error(flow.liquidate(rid)) == (422, "Every expense item must be verified before liquidation")
    flow.ok(flow.verify(rid, first))
    assert _error(flow.return_expenses(rid))[0] == 422
    flow.ok(flow.verify(rid, second, "RETURNED", reason="Wrong vendor"))
    assert flow.ok(flow.get(people.finance, rid))["allowed_actions"] == ["verify_items", "return_expenses"]
    assert _error(flow.liquidate(rid)) == (422, "Every expense item must be verified before liquidation")
    assert flow.return_expenses(rid, user=people.approver).status_code == 403

    body = flow.ok(flow.return_expenses(rid))
    assert body["status"] == "EXPENSE_RETURNED"
    returned = body["expense_items"][1]
    # D9: the returned item goes back to approval PENDING so it passes Stage 5 again.
    assert (returned["approval_status"], returned["verification_status"], returned["editable"]) == (
        "PENDING",
        "RETURNED",
        True,
    )
    assert returned["approved_by_name"] is None and returned["verification_reason"] == "Wrong vendor"
    assert body["expense_items"][0]["editable"] is False
    audit = _last_audit(db_session, rid)
    assert audit.action == "expenses_returned" and audit.snapshot["returned_item_ids"] == [second]
    assert _last_note(db_session, people.requestor) == (
        "expenses_returned",
        f"{body['ref_no']}: Finance Officer returned 1 expense item(s) of ₹20,000.00 for correction",
    )
    assert flow.ok(flow.get(people.requestor, rid))["allowed_actions"] == ["edit_expenses", "submit_expenses"]
    _consistent(db_session, flow.project)


def test_finance_return_sends_the_item_through_stage_5_again(db_session, flow, people):
    body = flow.approved_all("30000.00", "10000.00", "20000.00")
    rid = body["id"]
    first, second = body["expense_items"]
    flow.ok(flow.verify(rid, first["id"]))
    flow.ok(flow.verify(rid, second["id"], "RETURNED", reason="Wrong vendor"))
    flow.ok(flow.return_expenses(rid))

    doc = second["documents"][0]["id"]
    flow.ok(
        flow.save(rid, [expense_item("Item 1 (fixed)", "19500.00", document_ids=(doc,), id=second["id"])])
    )
    body = flow.ok(flow.submit(rid))
    states = [(i["approval_status"], i["verification_status"]) for i in body["expense_items"]]
    assert states == [("APPROVED", "VERIFIED"), ("PENDING", "PENDING")]
    assert flow.ok(flow.get(people.approver, rid))["allowed_actions"] == ["review_items"]
    assert flow.ok(flow.get(people.finance, rid))["allowed_actions"] == []  # not Finance's turn yet

    flow.ok(flow.review(rid, second["id"]))
    body = flow.ok(flow.approve_expenses(rid))
    assert body["status"] == "PENDING_VERIFICATION"
    assert flow.ok(flow.get(people.finance, rid))["allowed_actions"] == ["verify_items"]
    flow.ok(flow.verify(rid, second["id"]))
    assert flow.ok(flow.get(people.finance, rid))["allowed_actions"] == ["liquidate"]
    body = flow.ok(flow.liquidate(rid, balance_return_ref="RET-1"))
    assert body["liquidation"]["balance_returned"] == "500.00"
    _consistent(db_session, flow.project)


# ---------------------------------------------------------------------------
# Liquidation: both branches of arch §9.2
# ---------------------------------------------------------------------------


def _verified(flow, amount: str, *items: str) -> dict:
    body = flow.approved_all(amount, *items)
    for item in body["expense_items"]:
        flow.ok(flow.verify(body["id"], item["id"]))
    return flow.ok(flow.get(flow.people.finance, body["id"]))


def test_overspend_is_settled_with_a_top_up(db_session, flow, people):
    body = _verified(flow, "30000.00", "20000.00", "12000.00")
    rid = body["id"]
    assert (body["verified_total"], body["overage_reserved"]) == ("32000.00", "2000.00")
    assert body["allowed_actions"] == ["liquidate"]
    before = _consistent(db_session, flow.project)
    assert (before.reserved, before.outstanding, before.liquidated) == (
        D("2000.00"),
        D("30000.00"),
        D("0.00"),
    )

    body = flow.ok(flow.liquidate(rid, balance_return_ref="IGNORED-REF"))
    assert body["status"] == "LIQUIDATED" and body["allowed_actions"] == []
    assert body["overage_reserved"] == "0.00"
    liquidation = body["liquidation"]
    assert {k: v for k, v in liquidation.items() if k != "completed_at"} == {
        "verified_total": "32000.00",
        "disbursed_total": "30000.00",
        "balance_returned": "0.00",
        "balance_return_ref": None,
        "top_up_amount": "2000.00",
        "completed_by_name": "Finance Officer",
    }
    after = _consistent(db_session, flow.project)
    assert (after.reserved, after.outstanding, after.liquidated) == (D("0.00"), D("0.00"), D("32000.00"))
    assert after.available == D("68000.00")
    assert _ledger_tail(db_session, flow.project, 2) == [
        ("LIQUIDATE", D("32000.00")),
        ("TOPUP", D("2000.00")),
    ]

    audit = _last_audit(db_session, rid)
    assert (audit.action, audit.from_status, audit.to_status) == (
        "liquidated",
        "PENDING_VERIFICATION",
        "LIQUIDATED",
    )
    assert {
        k: audit.snapshot[k]
        for k in (
            "verified_total",
            "disbursed_total",
            "balance_returned",
            "balance_return_ref",
            "top_up_amount",
        )
    } == {
        "verified_total": "32000.00",
        "disbursed_total": "30000.00",
        "balance_returned": "0.00",
        "balance_return_ref": None,
        "top_up_amount": "2000.00",
    }
    message = (
        f"{body['ref_no']} was liquidated by Finance Officer: verified ₹32,000.00, top-up of ₹2,000.00 paid"
    )
    assert _last_note(db_session, people.requestor) == ("request_liquidated", message)
    assert _last_note(db_session, people.approver) == ("request_liquidated", message)


def test_underspend_needs_a_balance_return_reference(db_session, flow, people):
    body = _verified(flow, "30000.00", "15000.00", "10000.00")
    rid = body["id"]
    _consistent(db_session, flow.project)

    resp = flow.liquidate(rid)
    assert resp.status_code == 422
    assert resp.json()["error"] == {
        "code": "business_rule",
        "message": BALANCE_REF_REQUIRED,
        "details": {"verified_total": "25000.00", "disbursed_total": "30000.00", "balance_return": "5000.00"},
    }
    assert flow.liquidate(rid, balance_return_ref="   ").status_code == 422  # blank = missing
    assert flow.liquidate(rid, balance_return_ref="x" * 101).status_code == 400
    assert flow.get(people.finance, rid).json()["status"] == "PENDING_VERIFICATION"

    body = flow.ok(flow.liquidate(rid, balance_return_ref="  NEFT-RET-778 "))
    liquidation = body["liquidation"]
    assert (
        liquidation["balance_returned"],
        liquidation["balance_return_ref"],
        liquidation["top_up_amount"],
    ) == ("5000.00", "NEFT-RET-778", "0.00")
    after = _consistent(db_session, flow.project)
    assert (after.reserved, after.outstanding, after.liquidated) == (D("0.00"), D("0.00"), D("25000.00"))
    assert after.available == D("75000.00")
    assert _ledger_tail(db_session, flow.project, 2) == [
        ("LIQUIDATE", D("25000.00")),
        ("RETURN", D("5000.00")),
    ]
    assert _last_note(db_session, people.approver)[1].endswith(
        "verified ₹25,000.00, balance of ₹5,000.00 returned (ref NEFT-RET-778)"
    )


def test_exact_spend_needs_no_reference(db_session, flow):
    rid = _verified(flow, "30000.00", "30000.00")["id"]
    body = flow.ok(flow.liquidate(rid))
    assert body["liquidation"]["balance_returned"] == body["liquidation"]["top_up_amount"] == "0.00"
    after = _consistent(db_session, flow.project)
    assert (after.outstanding, after.liquidated) == (D("0.00"), D("30000.00"))
    assert _ledger_tail(db_session, flow.project, 1) == [("LIQUIDATE", D("30000.00"))]


def test_liquidate_permissions_and_duplicates(db_session, flow, people, make_user):
    rid = _verified(flow, "30000.00", "30000.00")["id"]
    assert flow.liquidate(rid, user=people.approver).status_code == 403
    assert flow.liquidate(rid, user=people.requestor).status_code == 403
    assert flow.liquidate(rid, user=make_user(Role.SUPER_ADMIN)).status_code == 403
    assert flow.liquidate(rid, version=1).status_code == 409

    ops = make_user(Role.SUPER_ADMIN, can_finance_ops=True, full_name="Ops Admin")
    body = flow.ok(flow.liquidate(rid, user=ops))
    assert body["liquidation"]["completed_by_name"] == "Ops Admin"
    resp = flow.liquidate(rid)
    assert resp.status_code == 409
    assert resp.json()["error"]["details"] == {"status": "LIQUIDATED", "action": "liquidate"}
    assert db_session.scalar(select(func.count(Liquidation.id)).where(Liquidation.request_id == rid)) == 1
    _consistent(db_session, flow.project)


def test_an_existing_liquidation_row_is_409(db_session, flow, people):
    rid = _verified(flow, "30000.00", "30000.00")["id"]
    db_session.add(
        Liquidation(
            request_id=rid,
            verified_total=D("1"),
            disbursed_total=D("1"),
            balance_returned=D("0"),
            top_up_amount=D("0"),
            completed_by=people.finance.id,
        )
    )
    db_session.flush()
    resp = flow.liquidate(rid)
    assert resp.status_code == 409
    assert resp.json()["error"]["message"] == "This request has already been liquidated"
    assert flow.get(people.finance, rid).json()["status"] == "PENDING_VERIFICATION"
    _consistent(db_session, flow.project)
