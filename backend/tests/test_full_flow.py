"""All six stages through the API (phase 4 §6): the project buckets are checked against arch §9.2 after
every stage, then the history and the notifications each person received."""

from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.modules.audit.models import Notification
from app.modules.finance import budget
from tests.conftest import JPG_BYTES, PNG_BYTES, expense_item


def _buckets(db, project) -> tuple[D, D, D, D]:
    """(reserved, outstanding, liquidated, available), after proving the ledger rebuilds the same totals."""
    db.refresh(project)
    assert budget.reconcile(db, project.id) == budget.buckets_of(project)
    b = budget.buckets_of(project)
    return b.reserved, b.outstanding, b.liquidated, b.available


def _types(db, user) -> list[str]:
    return list(
        db.scalars(select(Notification.type).where(Notification.user_id == user.id).order_by(Notification.id))
    )


def _row(item: dict, **changes) -> dict:
    """An item of the save body built from the detail, with *changes* applied."""
    return {
        "id": item["id"],
        "description": item["description"],
        "amount": item["amount"],
        "expense_date": item["expense_date"],
        "category": item["category"],
        "comment": item["comment"],
        "document_ids": [d["id"] for d in item["documents"]],
        **changes,
    }


def test_overspend_request_through_all_six_stages(client, db_session, flow, people, auth_header):
    p = flow.project
    R, A, F = people.requestor, people.approver, people.finance
    Z = D("0.00")

    # Stage 1: raise → the amount is reserved.
    rid = flow.raised(amount="30000.00")["id"]
    assert _buckets(db_session, p) == (D("30000.00"), Z, Z, D("70000.00"))

    # Stage 2: approve.
    flow.ok(flow.act(A, rid, "approve"))
    assert _buckets(db_session, p) == (D("30000.00"), Z, Z, D("70000.00"))

    # Stage 3: disburse → reserved becomes outstanding.
    flow.ok(flow.disburse(rid))
    assert _buckets(db_session, p) == (Z, D("30000.00"), Z, D("70000.00"))

    # Stage 4: draft with uploads (files first, linked through document_ids), then submit above requested.
    bus = flow.uploaded(rid, content=JPG_BYTES, name="bus.jpg")
    hotel = flow.uploaded(rid, content=PNG_BYTES, name="hotel.png")
    detail = flow.ok(
        flow.save(
            rid,
            [
                expense_item("Bus tickets", "20000.00", document_ids=(bus["id"],), category="Travel"),
                expense_item("Hotel", "12000.00", document_ids=(hotel["id"],), category="Lodging"),
            ],
            overspend_justification="Hotel rates went up",
        )
    )
    assert flow.ok(flow.get(R, rid))["allowed_actions"] == ["edit_expenses", "submit_expenses"]
    detail = flow.ok(flow.submit(rid))
    item_bus, item_hotel = detail["expense_items"]
    assert detail["overage_reserved"] == "2000.00"
    assert _buckets(db_session, p) == (D("2000.00"), D("30000.00"), Z, D("68000.00"))

    # Stage 5: hotel rejected → sent back → corrected (+ another proof) → resubmitted; bus stays approved.
    flow.ok(flow.review(rid, item_bus["id"]))
    flow.ok(flow.review(rid, item_hotel["id"], "REJECTED", reason="Need the itemised bill"))
    flow.ok(flow.send_back(rid))
    extra = flow.uploaded(rid, item_id=item_hotel["id"], name="hotel-itemised.pdf")
    detail = flow.ok(flow.get(R, rid))
    assert [d["id"] for d in detail["expense_items"][1]["documents"]] == [hotel["id"], extra["id"]]
    flow.ok(flow.save(rid, [_row(detail["expense_items"][1], amount="13000.00")]))
    detail = flow.ok(flow.submit(rid))
    assert detail["overage_reserved"] == "3000.00"
    assert _buckets(db_session, p) == (D("3000.00"), D("30000.00"), Z, D("67000.00"))
    assert flow.ok(flow.get(A, rid))["allowed_actions"] == ["review_items"]
    flow.ok(flow.review(rid, item_hotel["id"]))
    detail = flow.ok(flow.approve_expenses(rid))
    assert detail["status"] == "PENDING_VERIFICATION" and detail["approved_total"] == "33000.00"

    # Stage 6: Finance returns the hotel; it passes Stage 5 again before Finance sees it.
    flow.ok(flow.verify(rid, item_bus["id"]))
    flow.ok(flow.verify(rid, item_hotel["id"], "RETURNED", reason="Amount does not match the bill"))
    flow.ok(flow.return_expenses(rid))
    detail = flow.ok(flow.get(R, rid))
    flow.ok(flow.save(rid, [_row(detail["expense_items"][1], amount="12000.00")]))
    flow.ok(flow.submit(rid))
    assert _buckets(db_session, p) == (D("2000.00"), D("30000.00"), Z, D("68000.00"))
    flow.ok(flow.review(rid, item_hotel["id"]))
    flow.ok(flow.approve_expenses(rid))
    detail = flow.ok(flow.verify(rid, item_hotel["id"]))
    assert detail["allowed_actions"] == ["liquidate"] and detail["verified_total"] == "32000.00"
    detail = flow.ok(flow.liquidate(rid))
    assert detail["status"] == "LIQUIDATED" and detail["liquidation"]["top_up_amount"] == "2000.00"
    # arch §9.2 overspend: outstanding and overage clear; the verified total is liquidated.
    assert _buckets(db_session, p) == (Z, Z, D("32000.00"), D("68000.00"))

    # Every role sees no action on a liquidated request; proofs remain downloadable.
    for user in (R, A, F):
        assert flow.ok(flow.get(user, rid))["allowed_actions"] == []
    resp = client.get(f"/api/documents/{extra['id']}", headers=auth_header(A))
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/pdf"

    history = client.get(f"/api/requests/{rid}/history", headers=auth_header(R)).json()
    assert [h["action"] for h in history] == [
        "submitted",
        "approved",
        "disbursed",
        "document_uploaded",
        "document_uploaded",
        "expenses_saved",
        "expenses_submitted",
        "item_approved",
        "item_rejected",
        "expenses_sent_back",
        "document_uploaded",
        "expenses_saved",
        "expenses_submitted",
        "item_approved",
        "expenses_approved",
        "item_verified",
        "item_returned",
        "expenses_returned",
        "expenses_saved",
        "expenses_submitted",
        "item_approved",
        "expenses_approved",
        "item_verified",
        "liquidated",
    ]
    assert history[-1]["actor_name"] == "Finance Officer" and history[-1]["to_status"] == "LIQUIDATED"
    assert history[9]["snapshot"]["rejected_item_ids"] == [item_hotel["id"]]

    assert _types(db_session, R) == [
        "request_approved",
        "request_disbursed",
        "expenses_sent_back",
        "expenses_returned",
        "request_liquidated",
    ]
    assert _types(db_session, A) == [
        "request_submitted",
        "expenses_submitted",
        "expenses_submitted",
        "expenses_submitted",
        "request_liquidated",
    ]
    assert _types(db_session, F) == ["request_approved", "expenses_approved", "expenses_approved"]


@pytest.mark.parametrize(("spent", "branch"), [("26500.00", "RETURN"), ("30000.00", None)])
def test_underspend_and_exact_spend(db_session, flow, people, spent, branch):
    p = flow.project
    Z = D("0.00")
    rid = flow.at_stage4("30000.00")["id"]
    assert _buckets(db_session, p) == (Z, D("30000.00"), Z, D("70000.00"))
    flow.with_items(rid, spent)
    detail = flow.ok(flow.submit(rid))
    assert detail["overage_reserved"] == "0.00"
    assert _buckets(db_session, p) == (Z, D("30000.00"), Z, D("70000.00"))
    item = detail["expense_items"][0]["id"]
    flow.ok(flow.review(rid, item))
    flow.ok(flow.approve_expenses(rid))
    flow.ok(flow.verify(rid, item))
    assert _buckets(db_session, p) == (Z, D("30000.00"), Z, D("70000.00"))

    ref = {"balance_return_ref": "RET-42"} if branch else {}
    detail = flow.ok(flow.liquidate(rid, **ref))
    returned = D("30000.00") - D(spent)
    assert detail["liquidation"]["balance_returned"] == str(returned)
    assert detail["liquidation"]["balance_return_ref"] == ("RET-42" if branch else None)
    # arch §9.2 underspend: the unspent balance comes back to the project as available money.
    assert _buckets(db_session, p) == (Z, Z, D(spent), D("100000.00") - D(spent))
    tail = [r.entry_type for r in budget.ledger_rows(db_session, p.id)][-2:]
    assert tail == (["LIQUIDATE", "RETURN"] if branch else ["DISBURSE", "LIQUIDATE"])
