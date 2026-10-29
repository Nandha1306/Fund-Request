"""Stages 4–5 through the API (phase 4 §2–§3): save draft, submit guards, overage reservation, item review,
send-back / final approval guards, approved items locked across rounds (D9)."""

from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import select

from app.modules.audit.models import AuditLog, Notification
from app.modules.expenses.models import Document, ExpenseItem
from app.modules.finance import budget
from tests.conftest import expense_item


def _consistent(db, project) -> None:
    db.refresh(project)
    assert budget.reconcile(db, project.id) == budget.buckets_of(project)


def _audit(db, request_id) -> list[AuditLog]:
    return list(
        db.scalars(
            select(AuditLog)
            .where(AuditLog.entity == "fund_requests", AuditLog.entity_id == request_id)
            .order_by(AuditLog.id)
        )
    )


def _notes(db, user) -> list[tuple[str, str]]:
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.id))
    return [(n.type, n.message) for n in rows]


def _items(body) -> list[tuple[str, str, str]]:
    return [(i["description"], i["approval_status"], i["verification_status"]) for i in body["expense_items"]]


# ---------------------------------------------------------------------------
# Save draft
# ---------------------------------------------------------------------------


def test_save_creates_updates_and_deletes_items(db_session, flow, people):
    stage4 = flow.at_stage4()
    rid, version = stage4["id"], stage4["version"]
    assert stage4["allowed_actions"] == ["return"]  # Finance's view
    assert flow.ok(flow.get(people.requestor, rid))["allowed_actions"] == ["edit_expenses"]

    a, b, c = (flow.uploaded(rid, name=f"{n}.pdf") for n in "abc")
    body = flow.ok(
        flow.save(
            rid,
            [
                expense_item(
                    "Bus tickets", "1200.00", document_ids=(a["id"],), category="Travel", comment="  "
                ),
                expense_item(
                    "Hotel",
                    "8000.50",
                    document_ids=(b["id"], c["id"]),
                    category="Lodging",
                    comment="2 nights",
                ),
            ],
        )
    )
    assert body["version"] == version + 1 and body["status"] == "PENDING_EXPENSE_SUBMISSION"
    assert body["allowed_actions"] == ["edit_expenses", "submit_expenses"]
    bus, hotel = body["expense_items"]
    assert bus["id"] < hotel["id"]
    assert (bus["amount"], bus["category"], bus["comment"], bus["editable"]) == (
        "1200.00",
        "Travel",
        None,
        True,
    )
    assert (bus["approval_status"], bus["verification_status"]) == ("PENDING", "PENDING")
    assert [d["id"] for d in hotel["documents"]] == [b["id"], c["id"]]
    assert (body["expense_total"], body["approved_total"], body["verified_total"]) == (
        "9200.50",
        "0.00",
        "0.00",
    )
    assert body["unattached_documents"] == [] and body["liquidation"] is None
    assert body["overage_reserved"] == "0.00" and body["overspend_justification"] is None

    # Update the bus item (moving a hotel document onto it), drop the hotel item, add a new one.
    body = flow.ok(
        flow.save(
            rid,
            [
                expense_item("Bus tickets (return)", "1300", document_ids=(a["id"], c["id"]), id=bus["id"]),
                expense_item("Meals", "450.00", category="Meals"),
            ],
        )
    )
    assert [(i["description"], i["amount"]) for i in body["expense_items"]] == [
        ("Bus tickets (return)", "1300.00"),
        ("Meals", "450.00"),
    ]
    assert body["expense_items"][0]["id"] == bus["id"] and body["expense_items"][0]["category"] is None
    assert [d["id"] for d in body["expense_items"][0]["documents"]] == [a["id"], c["id"]]
    assert [d["id"] for d in body["unattached_documents"]] == [b["id"]]  # its item was deleted
    assert db_session.get(ExpenseItem, hotel["id"]) is None
    assert body["expense_total"] == "1750.00"

    # An empty list deletes every editable item; documents survive unattached.
    body = flow.ok(flow.save(rid, []))
    assert body["expense_items"] == [] and len(body["unattached_documents"]) == 3
    assert body["allowed_actions"] == ["edit_expenses"]

    saves = [r for r in _audit(db_session, rid) if r.action == "expenses_saved"]
    assert len(saves) == 3
    assert saves[0].snapshot["total"] == "9200.50" and len(saves[0].snapshot["items"]) == 2
    assert saves[0].from_status == saves[0].to_status == "PENDING_EXPENSE_SUBMISSION"


def test_save_sets_and_clears_the_justification(flow):
    rid = flow.at_stage4()["id"]
    body = flow.ok(flow.save(rid, [], overspend_justification="  Prices rose  "))
    assert body["overspend_justification"] == "Prices rose"
    assert flow.ok(flow.save(rid, []))["overspend_justification"] == "Prices rose"  # not sent → unchanged
    assert flow.ok(flow.save(rid, [], overspend_justification=None))["overspend_justification"] is None


@pytest.mark.parametrize(
    ("patch", "loc"),
    [
        ({"description": ""}, ["body", "items", 0, "description"]),
        ({"description": "x" * 501}, ["body", "items", 0, "description"]),
        ({"amount": "0"}, ["body", "items", 0, "amount"]),
        ({"amount": "12.345"}, ["body", "items", 0, "amount"]),
        ({"category": "x" * 101}, ["body", "items", 0, "category"]),
        ({"comment": "x" * 1001}, ["body", "items", 0, "comment"]),
        ({"expense_date": "yesterday"}, ["body", "items", 0, "expense_date"]),
    ],
)
def test_save_validation_is_400(flow, patch, loc):
    rid = flow.at_stage4()["id"]
    resp = flow.save(rid, [{**expense_item(), **patch}])
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["details"][0]["loc"] == loc


def test_justification_longer_than_1000_is_400(flow):
    rid = flow.at_stage4()["id"]
    resp = flow.save(rid, [], overspend_justification="x" * 1001)
    assert resp.status_code == 400
    assert resp.json()["error"]["details"][0]["loc"] == ["body", "overspend_justification"]
    assert flow.save(rid, [], overspend_justification="x" * 1000).status_code == 200


def test_save_reference_rules_are_422(flow):
    rid = flow.at_stage4()["id"]
    other = flow.at_stage4()["id"]
    foreign = flow.uploaded(other)
    other_item = flow.with_items(other, "100.00")["expense_items"][0]
    own = flow.uploaded(rid)

    cases = [
        (
            [expense_item(document_ids=(foreign["id"],))],
            f"Document {foreign['id']} does not belong to this request",
        ),
        (
            [expense_item(document_ids=(own["id"],)), expense_item(document_ids=(own["id"],))],
            f"Document {own['id']} is attached to more than one item",
        ),
        ([expense_item(id=other_item["id"])], f"Expense item {other_item['id']} is not on this request"),
    ]
    for items, message in cases:
        resp = flow.save(rid, items)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error"]["message"] == message

    item_id = flow.ok(flow.save(rid, [expense_item()]))["expense_items"][0]["id"]
    resp = flow.save(rid, [expense_item(id=item_id), expense_item(id=item_id)])
    assert (
        resp.status_code == 422
        and resp.json()["error"]["message"] == f"Expense item {item_id} is listed twice"
    )


def test_save_who_when_and_version(flow, people):
    rid = flow.at_stage4()["id"]
    assert flow.save(rid, [], user=people.requestor2).status_code == 404
    assert flow.save(rid, [], user=people.approver).status_code == 403
    assert flow.save(rid, [], user=people.finance).status_code == 403
    resp = flow.save(rid, [], version=1)
    assert resp.status_code == 409 and resp.json()["error"]["details"] == {
        "current_version": flow.version(rid)
    }

    pending = flow.raised()["id"]
    resp = flow.save(pending, [])
    assert resp.status_code == 409
    assert resp.json()["error"]["details"] == {"status": "PENDING_APPROVAL", "action": "save_expenses"}


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


def test_submit_guards(db_session, flow, people):
    rid = flow.at_stage4()["id"]

    def submit_error() -> tuple[int, str, object]:
        resp = flow.submit(rid)
        err = resp.json()["error"]
        return resp.status_code, err["message"], err["details"]

    assert submit_error() == (422, "Add at least one expense item before submitting", None)

    doc = flow.uploaded(rid)
    items = flow.ok(
        flow.save(rid, [expense_item("A", "100.00", document_ids=(doc["id"],)), expense_item("B", "200.00")])
    )["expense_items"]
    assert submit_error() == (
        422,
        "Every expense item needs at least one proof document",
        {"item_ids": [items[1]["id"]]},
    )

    doc2 = flow.uploaded(rid, item_id=items[1]["id"])
    tomorrow = date.today() + timedelta(days=1)
    flow.ok(
        flow.save(
            rid,
            [
                expense_item("A", "100.00", document_ids=(doc["id"],), id=items[0]["id"]),
                expense_item(
                    "B", "200.00", document_ids=(doc2["id"],), id=items[1]["id"], expense_date=tomorrow
                ),
            ],
        )
    )
    assert submit_error() == (422, "Expense dates cannot be in the future", {"item_ids": [items[1]["id"]]})

    flow.ok(
        flow.save(
            rid,
            [
                expense_item("A", "100.00", document_ids=(doc["id"],), id=items[0]["id"]),
                expense_item("B", "30000.00", document_ids=(doc2["id"],), id=items[1]["id"]),
            ],
        )
    )
    assert submit_error() == (
        422,
        "A justification is required when the expenses exceed the requested amount",
        {"expense_total": "30100.00", "amount_requested": "30000.00"},
    )
    assert (
        submit_error()[0] == 422
        and flow.get(people.finance, rid).json()["status"] == "PENDING_EXPENSE_SUBMISSION"
    )
    _consistent(db_session, flow.project)

    # Requestor only; Stage 4 only.
    flow.ok(
        flow.save(
            rid,
            [
                expense_item("A", "100.00", document_ids=(doc["id"],), id=items[0]["id"]),
                expense_item("B", "29900.00", document_ids=(doc2["id"],), id=items[1]["id"]),
            ],
        )
    )
    assert flow.submit(rid, user=people.approver).status_code == 403
    assert flow.submit(rid, user=people.requestor2).status_code == 404
    assert flow.submit(rid, version=1).status_code == 409
    body = flow.ok(flow.submit(rid))
    assert body["status"] == "PENDING_EXPENSE_APPROVAL" and body["overage_reserved"] == "0.00"
    resp = flow.submit(rid)
    assert resp.status_code == 409
    assert resp.json()["error"]["details"] == {
        "status": "PENDING_EXPENSE_APPROVAL",
        "action": "submit_expenses",
    }


def test_submit_reserves_the_overage_audits_and_notifies(db_session, flow, people):
    rid = flow.at_stage4("30000.00")["id"]
    flow.with_items(rid, "20000.00", "12000.00", justification="Fuel prices went up")
    _consistent(db_session, flow.project)
    before = budget.buckets_of(flow.project)

    body = flow.ok(flow.submit(rid))
    assert (body["status"], body["expense_total"], body["overage_reserved"]) == (
        "PENDING_EXPENSE_APPROVAL",
        "32000.00",
        "2000.00",
    )
    assert body["allowed_actions"] == []  # Finance's view
    _consistent(db_session, flow.project)
    assert flow.project.reserved == before.reserved + D("2000.00")
    assert flow.project.outstanding == before.outstanding == D("30000.00")
    assert [(r.entry_type, r.amount, r.note) for r in budget.ledger_rows(db_session, flow.project.id)][
        -1
    ] == ("RESERVE", D("2000.00"), "overage")

    row = _audit(db_session, rid)[-1]
    assert (row.action, row.from_status, row.to_status) == (
        "expenses_submitted",
        "PENDING_EXPENSE_SUBMISSION",
        "PENDING_EXPENSE_APPROVAL",
    )
    assert row.snapshot["total"] == "32000.00" and len(row.snapshot["items"]) == 2
    assert row.snapshot["overage_reserved"] == "2000.00"
    assert _notes(db_session, people.approver)[-1] == (
        "expenses_submitted",
        f"{body['ref_no']}: Requestor A submitted expenses of ₹32,000.00 for your review",
    )
    approver_view = flow.ok(flow.get(people.approver, rid))
    assert approver_view["allowed_actions"] == ["review_items"]
    assert flow.ok(flow.get(people.requestor, rid))["allowed_actions"] == []


def test_submit_is_422_when_the_project_cannot_cover_the_overage(db_session, flow, people):
    rid = flow.at_stage4("30000.00")["id"]
    flow.raised(amount="69000.00")  # leaves 1,000.00 available
    flow.with_items(rid, "32000.00", justification="Needed")
    resp = flow.submit(rid)
    assert resp.status_code == 422
    assert resp.json()["error"] == {
        "code": "business_rule",
        "message": "Amount exceeds the project's available budget",
        "details": {"available": "1000.00", "required": "2000.00"},
    }
    body = flow.ok(flow.get(people.requestor, rid))
    assert body["status"] == "PENDING_EXPENSE_SUBMISSION" and body["overage_reserved"] == "0.00"
    _consistent(db_session, flow.project)


# ---------------------------------------------------------------------------
# Review, send back, final approval
# ---------------------------------------------------------------------------


def test_review_guards(db_session, flow, people):
    body = flow.submitted("30000.00", "10000.00", "20000.00")
    rid = body["id"]
    first, second = body["expense_items"]

    resp = flow.review(rid, first["id"], "REJECTED")
    assert resp.status_code == 400 and resp.json()["error"]["code"] == "validation_error"
    assert flow.review(rid, first["id"], "REJECTED", reason="   ").status_code == 400
    assert flow.review(rid, first["id"], "MAYBE").status_code == 400
    assert flow.review(rid, first["id"], user=people.approver2).status_code == 404
    assert flow.review(rid, first["id"], user=people.finance).status_code == 403
    assert flow.review(rid, 999999).status_code == 404
    other = flow.submitted("1000.00")
    assert flow.review(rid, other["expense_items"][0]["id"]).status_code == 404

    version = flow.version(rid)
    body = flow.ok(flow.review(rid, first["id"], reason="ignored"))
    assert body["version"] == version + 1 and body["status"] == "PENDING_EXPENSE_APPROVAL"
    reviewed = body["expense_items"][0]
    assert (reviewed["approval_status"], reviewed["approval_reason"], reviewed["approved_by_name"]) == (
        "APPROVED",
        None,
        "Approver A",
    )
    assert reviewed["approved_at"] and reviewed["editable"] is False
    assert body["approved_total"] == "10000.00"
    resp = flow.review(rid, first["id"])
    assert (
        resp.status_code == 409 and resp.json()["error"]["message"] == "This expense item is already approved"
    )
    assert flow.review(rid, second["id"], version=version).status_code == 409  # stale screen

    # Rejected, then changed to approved: allowed (only APPROVED is final).
    body = flow.ok(flow.review(rid, second["id"], "REJECTED", reason="No receipt date"))
    assert body["expense_items"][1]["approval_reason"] == "No receipt date"
    body = flow.ok(flow.review(rid, second["id"]))
    assert body["expense_items"][1]["approval_status"] == "APPROVED"

    rows = [r for r in _audit(db_session, rid) if r.action.startswith("item_")]
    assert [(r.action, r.reason) for r in rows] == [
        ("item_approved", None),
        ("item_rejected", "No receipt date"),
        ("item_approved", None),
    ]
    assert rows[1].snapshot["item_id"] == second["id"]
    assert (rows[1].snapshot["description"], rows[1].snapshot["amount"]) == ("Item 1", "20000.00")


def test_send_back_and_final_approval_guards(db_session, flow, people):
    body = flow.submitted("30000.00", "10000.00", "20000.00")
    rid = body["id"]
    first, second = (i["id"] for i in body["expense_items"])

    def error(resp) -> tuple[int, str]:
        return resp.status_code, resp.json()["error"]["message"]

    assert error(flow.send_back(rid)) == (422, "Review every expense item before sending the expenses back")
    assert error(flow.approve_expenses(rid)) == (
        422,
        "Every expense item must be approved before final approval",
    )
    flow.ok(flow.review(rid, first))
    assert flow.ok(flow.get(people.approver, rid))["allowed_actions"] == ["review_items"]
    assert error(flow.send_back(rid)) == (422, "Review every expense item before sending the expenses back")
    flow.ok(flow.review(rid, second, "REJECTED", reason="Duplicate"))
    assert flow.ok(flow.get(people.approver, rid))["allowed_actions"] == [
        "review_items",
        "send_back_expenses",
    ]
    assert error(flow.approve_expenses(rid)) == (
        422,
        "Every expense item must be approved before final approval",
    )
    assert flow.send_back(rid, user=people.approver2).status_code == 404
    assert flow.send_back(rid, user=people.requestor).status_code == 403

    body = flow.ok(flow.send_back(rid))
    assert body["status"] == "EXPENSE_RETURNED"
    assert _items(body) == [("Item 0", "APPROVED", "PENDING"), ("Item 1", "REJECTED", "PENDING")]
    assert [i["editable"] for i in body["expense_items"]] == [False, True]
    assert body["overage_reserved"] == "0.00"
    assert _audit(db_session, rid)[-1].snapshot["rejected_item_ids"] == [second]
    assert _notes(db_session, people.requestor)[-1] == (
        "expenses_sent_back",
        f"{body['ref_no']}: Approver A sent your expenses back; 1 item(s) of ₹20,000.00 were rejected",
    )
    assert flow.ok(flow.get(people.requestor, rid))["allowed_actions"] == ["edit_expenses", "submit_expenses"]
    assert flow.ok(flow.get(people.approver, rid))["allowed_actions"] == []


def test_approved_items_stay_locked_across_rounds(db_session, flow, people):
    body = flow.submitted("30000.00", "10000.00", "20000.00")
    rid = body["id"]
    kept, fixed = body["expense_items"]
    flow.ok(flow.review(rid, kept["id"]))
    flow.ok(flow.review(rid, fixed["id"], "REJECTED", reason="Wrong amount"))
    flow.ok(flow.send_back(rid))

    kept_doc = kept["documents"][0]["id"]
    fixed_doc = fixed["documents"][0]["id"]
    # Changing a locked item — or stealing its document — is refused.
    resp = flow.save(rid, [expense_item("Item 0", "9999.00", document_ids=(kept_doc,), id=kept["id"])])
    assert resp.status_code == 422
    assert (
        resp.json()["error"]["message"]
        == f"Expense item {kept['id']} is approved and locked; it cannot be changed"
    )
    resp = flow.save(
        rid, [expense_item("Item 1", "19000.00", document_ids=(fixed_doc, kept_doc), id=fixed["id"])]
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == f"Document {kept_doc} belongs to an approved expense item"

    # Omitted → untouched; sent back unchanged → accepted.  The rejected item is corrected; one is added.
    extra = flow.uploaded(rid, name="taxi.pdf")
    body = flow.ok(
        flow.save(
            rid,
            [
                expense_item(
                    "Item 1", "19000.00", document_ids=(fixed_doc,), id=fixed["id"], category="Travel"
                ),
                expense_item("Taxi", "500.00", document_ids=(extra["id"],)),
            ],
        )
    )
    assert [i["id"] for i in body["expense_items"]][:2] == [kept["id"], fixed["id"]]
    assert _items(body) == [
        ("Item 0", "APPROVED", "PENDING"),
        ("Item 1", "REJECTED", "PENDING"),
        ("Taxi", "PENDING", "PENDING"),
    ]
    assert [d["id"] for d in body["expense_items"][0]["documents"]] == [kept_doc]
    same = {
        "description": "Item 0",
        "amount": "10000.00",
        "expense_date": kept["expense_date"],
        "category": "Travel",
        "comment": None,
        "document_ids": [kept_doc],
        "id": kept["id"],
    }
    body = flow.ok(
        flow.save(
            rid,
            [
                same,
                *[
                    {
                        **expense_item(
                            i["description"], i["amount"], document_ids=tuple(d["id"] for d in i["documents"])
                        ),
                        "id": i["id"],
                        "category": i["category"],
                    }
                    for i in body["expense_items"][1:]
                ],
            ],
        )
    )
    assert body["expense_total"] == "29500.00"

    body = flow.ok(flow.submit(rid))
    assert _items(body) == [
        ("Item 0", "APPROVED", "PENDING"),
        ("Item 1", "PENDING", "PENDING"),
        ("Taxi", "PENDING", "PENDING"),
    ]
    assert body["expense_items"][1]["approval_reason"] is None
    for item in body["expense_items"][1:]:
        flow.ok(flow.review(rid, item["id"]))
    assert flow.ok(flow.get(people.approver, rid))["allowed_actions"] == ["approve_expenses"]
    body = flow.ok(flow.approve_expenses(rid))
    assert body["status"] == "PENDING_VERIFICATION" and body["approved_total"] == "29500.00"
    assert db_session.scalar(select(Document.id).where(Document.expense_item_id.is_(None))) is None
    _consistent(db_session, flow.project)


def test_final_approval_notifies_finance(db_session, flow, people, make_user):
    from app.core.enums import Role

    ops = make_user(Role.SUPER_ADMIN, can_finance_ops=True, full_name="Ops Admin")
    make_user(Role.SUPER_ADMIN, full_name="Plain Admin")
    make_user(Role.FINANCE, is_active=False)
    body = flow.approved_all("30000.00", "12000.00", "18000.00")
    assert body["status"] == "PENDING_VERIFICATION" and body["approved_total"] == "30000.00"
    message = f"{body['ref_no']}: expenses of ₹30,000.00 were approved by Approver A and await verification"
    assert _notes(db_session, people.finance)[-1] == ("expenses_approved", message)
    assert _notes(db_session, ops)[-1] == ("expenses_approved", message)
    recipients = db_session.scalars(
        select(Notification.user_id).where(Notification.type == "expenses_approved")
    )
    assert set(recipients) == {people.finance.id, ops.id}
    assert flow.ok(flow.get(people.finance, body["id"]))["allowed_actions"] == ["verify_items"]
    assert flow.ok(flow.get(people.approver, body["id"]))["allowed_actions"] == []


def test_resubmission_adjusts_the_overage(db_session, flow, people):
    body = flow.submitted("30000.00", "20000.00", "12000.00")
    rid = body["id"]
    assert body["overage_reserved"] == "2000.00" and body["overspend_justification"] == "Prices went up"
    _consistent(db_session, flow.project)
    kept, fixed = body["expense_items"]
    flow.ok(flow.review(rid, kept["id"]))
    flow.ok(flow.review(rid, fixed["id"], "REJECTED", reason="Too much"))
    flow.ok(flow.send_back(rid))
    assert (
        flow.ok(flow.get(people.requestor, rid))["overage_reserved"] == "2000.00"
    )  # held until resubmission

    doc = fixed["documents"][0]["id"]
    flow.ok(flow.save(rid, [expense_item("Item 1", "1000.00", document_ids=(doc,), id=fixed["id"])]))
    body = flow.ok(flow.submit(rid))
    assert (body["expense_total"], body["overage_reserved"]) == ("21000.00", "0.00")
    assert [(r.entry_type, r.amount, r.note) for r in budget.ledger_rows(db_session, flow.project.id)][
        -1
    ] == ("RELEASE", D("2000.00"), "overage")
    _consistent(db_session, flow.project)
    assert flow.project.reserved == D("0.00")
