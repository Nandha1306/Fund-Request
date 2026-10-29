"""Request API: raise, approve / reject / resubmit, reassign, withdraw, disburse, return, history,
``mine=action``, filters and pagination (phase 3 §3)."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select

from app.core.enums import ProjectStatus, RequestStatus, Role
from app.modules.audit.models import AuditLog, Notification
from app.modules.finance import budget
from app.modules.finance.models import Disbursement
from app.modules.fund_requests import workflow
from app.modules.fund_requests.models import FundRequest

S = RequestStatus


def _ledger(db, project) -> list[tuple[str, D]]:
    return [(r.entry_type, r.amount) for r in budget.ledger_rows(db, project.id)]


def _audit(db, request_id) -> list[str]:
    return list(
        db.scalars(
            select(AuditLog.action)
            .where(AuditLog.entity == "fund_requests", AuditLog.entity_id == request_id)
            .order_by(AuditLog.id)
        )
    )


def _notes(db, user) -> list[tuple[str, str]]:
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.id))
    return [(n.type, n.message) for n in rows]


def _consistent(db, project) -> None:
    db.refresh(project)
    assert budget.reconcile(db, project.id) == budget.buckets_of(project)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_reserves_audits_and_notifies(client, db_session, flow, people):
    body = flow.raised(amount="30000", supporting_info="  ")
    assert re.fullmatch(rf"FR-{date.today().year}-\d{{4,}}", body["ref_no"])
    assert body["status"] == "PENDING_APPROVAL" and body["version"] == 1
    assert (body["amount_requested"], body["amount_approved"], body["reserved_amount"]) == (
        "30000.00",
        None,
        "30000.00",
    )
    assert (body["disbursed_total"], body["disbursable_amount"]) == ("0.00", "0.00")
    assert body["supporting_info"] is None
    assert body["project"] == {
        "id": flow.project.id,
        "code": flow.project.code,
        "name": flow.project.name,
        "status": "ACTIVE",
    }
    assert body["requestor"]["full_name"] == "Requestor A" and body["approver"]["id"] == people.approver.id
    assert body["allowed_actions"] == [] and body["disbursements"] == []

    assert _ledger(db_session, flow.project)[-1] == ("RESERVE", D("30000.00"))
    _consistent(db_session, flow.project)
    assert _audit(db_session, body["id"]) == ["submitted"]
    assert _notes(db_session, people.approver) == [
        ("request_submitted", f"{body['ref_no']} from Requestor A needs your approval (₹30,000.00)")
    ]


def test_ref_numbers_come_from_the_sequence(flow):
    first, second = flow.raised(amount="10"), flow.raised(amount="10")
    n1, n2 = (int(r["ref_no"].rsplit("-", 1)[1]) for r in (first, second))
    assert n2 == n1 + 1


@pytest.mark.parametrize("case", ["non_member", "inactive", "missing"])
def test_create_on_an_ineligible_project_is_422(flow, people, make_project, case):
    if case == "non_member":
        project = make_project(members=(people.requestor2,))
    elif case == "inactive":
        project = make_project(status=ProjectStatus.INACTIVE, members=(people.requestor,))
    else:
        project = type("P", (), {"id": 999_999})()
    resp = flow.create(project=project)
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == (
        "You can only raise requests on active projects you are a member of"
    )


@pytest.mark.parametrize("case", ["self", "inactive", "finance", "missing"])
def test_create_with_an_invalid_approver_is_422(flow, people, make_user, case):
    approver = {
        "self": people.requestor,
        "inactive": make_user(Role.APPROVER, is_active=False),
        "finance": people.finance,
        "missing": type("U", (), {"id": 999_999})(),
    }[case]
    resp = flow.create(approver=approver)
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == "Approver must be an active approver other than the requestor"


def test_create_above_available_is_422_and_leaves_nothing(db_session, flow):
    flow.raised(amount="90000")
    resp = flow.create(amount="10000.01")
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == {"available": "10000.00"}
    assert db_session.scalar(select(func.count(FundRequest.id))) == 1
    _consistent(db_session, flow.project)
    assert flow.project.reserved == D("90000.00")


@pytest.mark.parametrize(
    "extra",
    [
        {"purpose": ""},
        {"purpose": "   "},
        {"purpose": "x" * 2001},
        {"supporting_info": "x" * 4001},
        {"amount_requested": "0"},
        {"amount_requested": "-5"},
        {"amount_requested": "10.001"},
        {"amount_requested": "100000000.00"},
    ],
)
def test_create_validates_input_with_400(flow, extra):
    assert flow.create(**extra).status_code == 400


def test_only_requestors_can_create(flow, people):
    assert flow.create(user=people.approver).status_code == 403
    assert flow.create(user=people.finance).status_code == 403


# ---------------------------------------------------------------------------
# Reject → resubmit → approve
# ---------------------------------------------------------------------------


def test_reject_resubmit_approve(db_session, flow, people, make_user):
    admin_ops = make_user(Role.SUPER_ADMIN, can_finance_ops=True)
    admin_plain = make_user(Role.SUPER_ADMIN)
    req = flow.raised(amount="30000")
    rid, ref = req["id"], req["ref_no"]

    assert flow.ok(flow.get(people.approver, rid))["allowed_actions"] == ["approve", "reject"]
    rejected = flow.ok(flow.act(people.approver, rid, "reject", reason="  Split travel and lodging  "))
    assert rejected["status"] == "RETURNED_FOR_CORRECTION" and rejected["reserved_amount"] == "0.00"
    assert _notes(db_session, people.requestor) == [
        ("request_rejected", f"{ref} was rejected by Approver A: Split travel and lodging")
    ]
    _consistent(db_session, flow.project)

    resubmitted = flow.ok(flow.resubmit(people.requestor, rid, amount="25000", approver=people.approver2))
    assert resubmitted["status"] == "PENDING_APPROVAL"
    assert resubmitted["approver"]["id"] == people.approver2.id
    assert (resubmitted["amount_requested"], resubmitted["reserved_amount"]) == ("25000.00", "25000.00")
    assert resubmitted["purpose"] == "Field visit (corrected)"
    assert _notes(db_session, people.approver2)[-1] == (
        "request_submitted",
        f"{ref} was resubmitted by Requestor A and needs your approval (₹25,000.00)",
    )

    approved = flow.ok(flow.act(people.approver2, rid, "approve"))
    assert approved["status"] == "PENDING_DISBURSEMENT"
    assert (approved["amount_approved"], approved["disbursable_amount"]) == ("25000.00", "25000.00")
    assert approved["allowed_actions"] == []  # the approver cannot disburse
    assert _notes(db_session, people.requestor)[-1] == (
        "request_approved",
        f"{ref} was approved by Approver B",
    )
    finance_msg = (
        "request_approved",
        f"{ref} was approved by Approver B and is awaiting disbursement (₹25,000.00)",
    )
    assert _notes(db_session, people.finance) == [finance_msg]
    assert _notes(db_session, admin_ops) == [finance_msg]
    assert _notes(db_session, admin_plain) == []

    assert _audit(db_session, rid) == ["submitted", "rejected", "resubmitted", "approved"]
    assert [e[0] for e in _ledger(db_session, flow.project)] == ["ALLOCATE", "RESERVE", "RELEASE", "RESERVE"]
    _consistent(db_session, flow.project)


@pytest.mark.parametrize("reason", [None, "", "   ", "x" * 1001])
@pytest.mark.parametrize("action", ["reject", "return"])
def test_reason_is_mandatory_400(flow, people, action, reason):
    rid = flow.raised()["id"]
    if action == "return":
        flow.ok(flow.act(people.approver, rid, "approve"))
    user = people.approver if action == "reject" else people.finance
    body = {} if reason is None else {"reason": reason}
    assert flow.act(user, rid, action, **body).status_code == 400


def test_resubmit_on_an_inactive_project_is_422(db_session, client, flow, people, auth_header):
    rid = flow.raised()["id"]
    flow.ok(flow.act(people.approver, rid, "reject", reason="no"))
    patch = client.patch(
        f"/api/projects/{flow.project.id}", json={"status": "INACTIVE"}, headers=auth_header(people.finance)
    )
    assert patch.status_code == 200
    resp = flow.resubmit(people.requestor, rid)
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == "This project is inactive; the request cannot be resubmitted"


def test_resubmit_with_an_invalid_approver_is_422(flow, people):
    rid = flow.raised()["id"]
    flow.ok(flow.act(people.approver, rid, "reject", reason="no"))
    assert flow.resubmit(people.requestor, rid, approver=people.finance).status_code == 422


def test_resubmit_above_available_is_422(db_session, flow, people):
    rid = flow.raised(amount="30000")["id"]
    flow.ok(flow.act(people.approver, rid, "reject", reason="no"))
    resp = flow.resubmit(people.requestor, rid, amount="100000.01")
    assert resp.status_code == 422 and resp.json()["error"]["details"] == {"available": "100000.00"}
    body = flow.ok(flow.get(people.requestor, rid))
    assert body["status"] == "RETURNED_FOR_CORRECTION" and body["amount_requested"] == "30000.00"
    _consistent(db_session, flow.project)


def test_stale_version_is_409(flow, people):
    req = flow.raised()
    resp = flow.act(people.approver, req["id"], "approve", version=req["version"] + 1)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert resp.json()["error"]["message"] == workflow.STALE_VERSION
    assert workflow.STALE_VERSION == "This request was changed by someone else. Reload and try again."
    flow.ok(flow.act(people.approver, req["id"], "reject", reason="no", version=req["version"]))
    resp = flow.resubmit(people.requestor, req["id"], version=req["version"])
    assert resp.status_code == 409


def test_wrong_state_is_409(flow, people):
    rid = flow.raised()["id"]
    resp = flow.act(people.requestor, rid, "withdraw")
    assert resp.status_code == 409
    assert resp.json()["error"]["details"] == {"status": "PENDING_APPROVAL", "action": "withdraw"}


def test_permissions_are_403(flow, people):
    rid = flow.raised()["id"]
    assert flow.act(people.requestor, rid, "approve").status_code == 403
    assert flow.act(people.finance, rid, "reject", reason="x").status_code == 403
    assert flow.act(people.approver, rid, "reassign", approver_id=people.approver2.id).status_code == 403
    assert flow.act(people.approver, rid, "return", reason="x").status_code == 403
    assert flow.disburse(rid, user=people.approver).status_code == 403


# ---------------------------------------------------------------------------
# Reassign
# ---------------------------------------------------------------------------


def test_reassign_moves_the_request_and_notifies_both(db_session, flow, people):
    req = flow.raised(amount="30000")
    body = flow.ok(flow.act(people.finance, req["id"], "reassign", approver_id=people.approver2.id))
    assert body["status"] == "PENDING_APPROVAL" and body["approver"]["full_name"] == "Approver B"
    assert body["version"] == 2

    ref = req["ref_no"]
    assert _notes(db_session, people.approver2) == [
        ("request_reassigned", f"{ref} from Requestor A was reassigned to you for approval (₹30,000.00)")
    ]
    assert _notes(db_session, people.approver)[-1] == (
        "request_reassigned",
        f"{ref} was reassigned from you to Approver B",
    )
    row = db_session.scalar(
        select(AuditLog).where(AuditLog.entity_id == req["id"], AuditLog.action == "reassigned")
    )
    assert (row.snapshot["old_approver_id"], row.snapshot["new_approver_id"]) == (
        people.approver.id,
        people.approver2.id,
    )
    names = (row.snapshot["old_approver_name"], row.snapshot["new_approver_name"])
    assert names == ("Approver A", "Approver B")
    assert flow.get(people.approver, req["id"]).status_code == 404  # no longer theirs
    assert flow.ok(flow.act(people.approver2, req["id"], "approve"))["status"] == "PENDING_DISBURSEMENT"


@pytest.mark.parametrize("target", ["current", "requestor", "inactive"])
def test_reassign_to_an_invalid_approver_is_422(flow, people, make_user, target):
    rid = flow.raised()["id"]
    approver = {
        "current": people.approver,
        "requestor": people.requestor,
        "inactive": make_user(Role.APPROVER, is_active=False),
    }[target]
    assert flow.act(people.finance, rid, "reassign", approver_id=approver.id).status_code == 422


def test_reassign_only_while_pending_approval(flow, people):
    rid = flow.raised()["id"]
    flow.ok(flow.act(people.approver, rid, "approve"))
    assert flow.act(people.finance, rid, "reassign", approver_id=people.approver2.id).status_code == 409


# ---------------------------------------------------------------------------
# Withdraw
# ---------------------------------------------------------------------------


def test_withdraw_from_returned(db_session, flow, people):
    req = flow.raised()
    flow.ok(flow.act(people.approver, req["id"], "reject", reason="no"))
    body = flow.ok(flow.act(people.requestor, req["id"], "withdraw"))
    assert body["status"] == "WITHDRAWN" and body["allowed_actions"] == []
    assert _notes(db_session, people.approver)[-1] == (
        "request_withdrawn",
        f"{req['ref_no']} was withdrawn by Requestor A",
    )
    _consistent(db_session, flow.project)
    assert flow.project.reserved == D("0.00")


def test_withdraw_is_blocked_after_a_payout(db_session, flow, people):
    rid = flow.raised()["id"]
    flow.ok(flow.act(people.approver, rid, "approve"))
    flow.ok(flow.disburse(rid))
    flow.ok(flow.act(people.finance, rid, "return", reason="Receipts missing"))
    resp = flow.act(people.requestor, rid, "withdraw")
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == "A request that has received a disbursement cannot be withdrawn"


# ---------------------------------------------------------------------------
# Disburse
# ---------------------------------------------------------------------------


def _approved(flow, people, amount="30000") -> int:
    rid = flow.raised(amount=amount)["id"]
    flow.ok(flow.act(people.approver, rid, "approve"))
    return rid


def test_disburse_pays_the_approved_amount(db_session, flow, people):
    rid = _approved(flow, people)
    assert flow.ok(flow.get(people.finance, rid))["allowed_actions"] == ["disburse", "return"]
    body = flow.ok(flow.disburse(rid, txn_ref="  NEFT-88231 ", notes="  "))
    assert body["status"] == "PENDING_EXPENSE_SUBMISSION"
    assert (body["reserved_amount"], body["disbursed_total"], body["disbursable_amount"]) == (
        "0.00",
        "30000.00",
        "0.00",
    )
    [d] = body["disbursements"]
    assert (d["amount"], d["txn_ref"], d["notes"], d["created_by_name"]) == (
        "30000.00",
        "NEFT-88231",
        None,
        "Finance Officer",
    )
    assert d["disbursed_on"] == str(date.today())
    assert _ledger(db_session, flow.project)[-1] == ("DISBURSE", D("30000.00"))
    _consistent(db_session, flow.project)
    assert (flow.project.reserved, flow.project.outstanding) == (D("0.00"), D("30000.00"))
    ref = body["ref_no"]
    assert _notes(db_session, people.requestor)[-1] == (
        "request_disbursed",
        f"₹30,000.00 was disbursed for {ref} (txn NEFT-88231)",
    )
    assert _audit(db_session, rid)[-1] == "disbursed"
    row = db_session.scalar(select(AuditLog).where(AuditLog.entity_id == rid, AuditLog.action == "disbursed"))
    assert (row.snapshot["amount"], row.snapshot["txn_ref"]) == ("30000.00", "NEFT-88231")


def test_disburse_same_key_again_returns_the_detail_without_a_second_row(db_session, flow, people):
    rid = _approved(flow, people)
    first = flow.ok(flow.disburse(rid, key="same-key-123"))
    again = flow.ok(flow.disburse(rid, key="same-key-123", version=1))  # stale version is irrelevant
    assert again["version"] == first["version"] and again["disbursements"] == first["disbursements"]
    assert db_session.scalar(select(func.count(Disbursement.id))) == 1
    _consistent(db_session, flow.project)


def test_disburse_key_reused_on_another_request_is_409(flow, people):
    first, second = _approved(flow, people, "1000"), _approved(flow, people, "1000")
    flow.ok(flow.disburse(first, key="shared-key-1"))
    assert flow.disburse(second, key="shared-key-1").status_code == 409


@pytest.mark.parametrize("headers", [{}, {"Idempotency-Key": "short"}, {"Idempotency-Key": "k" * 101}])
def test_disburse_needs_a_valid_idempotency_key_400(flow, people, headers):
    rid = _approved(flow, people)
    body = {"txn_ref": "T-1", "disbursed_on": str(date.today())}
    resp = flow.act(people.finance, rid, "disburse", headers=headers, **body)
    assert resp.status_code == 400


def test_disburse_in_the_future_is_422(flow, people):
    rid = _approved(flow, people)
    resp = flow.disburse(rid, disbursed_on=str(date.today() + timedelta(days=1)))
    assert resp.status_code == 422
    assert resp.json()["error"]["message"] == "The disbursement date cannot be in the future"


@pytest.mark.parametrize("txn_ref", ["", "   ", "x" * 101])
def test_disburse_validates_txn_ref_400(flow, people, txn_ref):
    rid = _approved(flow, people)
    assert flow.disburse(rid, txn_ref=txn_ref).status_code == 400


def test_disburse_with_a_used_txn_ref_is_409(db_session, flow, people):
    first, second = _approved(flow, people, "1000"), _approved(flow, people, "1000")
    flow.ok(flow.disburse(first, txn_ref="NEFT-1"))
    resp = flow.disburse(second, txn_ref="NEFT-1")
    assert resp.status_code == 409
    assert resp.json()["error"] == {
        "code": "duplicate_txn_ref",
        "message": "This transaction reference has already been used",
        "details": None,
    }
    assert flow.ok(flow.get(people.finance, second))["status"] == "PENDING_DISBURSEMENT"
    _consistent(db_session, flow.project)


# ---------------------------------------------------------------------------
# Return
# ---------------------------------------------------------------------------


def test_return_from_pending_disbursement_releases_the_reservation(db_session, flow, people):
    rid = _approved(flow, people)
    body = flow.ok(flow.act(people.finance, rid, "return", reason="Wrong cost centre"))
    assert body["status"] == "RETURNED_FOR_CORRECTION" and body["reserved_amount"] == "0.00"
    assert body["allowed_actions"] == []  # Finance's view
    assert _ledger(db_session, flow.project)[-1] == ("RELEASE", D("30000.00"))
    assert _notes(db_session, people.requestor)[-1] == (
        "request_returned",
        f"{body['ref_no']} was returned to you by Finance Officer: Wrong cost centre",
    )
    _consistent(db_session, flow.project)


def test_return_after_payout_keeps_the_disbursement(db_session, flow, people):
    rid = _approved(flow, people)
    flow.ok(flow.disburse(rid))
    body = flow.ok(flow.act(people.finance, rid, "return", reason="Receipts missing"))
    assert body["status"] == "RETURNED_FOR_CORRECTION"
    assert body["disbursed_total"] == "30000.00" and len(body["disbursements"]) == 1
    _consistent(db_session, flow.project)
    assert flow.project.outstanding == D("30000.00")
    assert flow.ok(flow.get(people.requestor, rid))["allowed_actions"] == ["resubmit"]


# ---------------------------------------------------------------------------
# History, subject label
# ---------------------------------------------------------------------------


def test_history_is_oldest_first_with_names(flow, people):
    rid = flow.raised()["id"]
    flow.ok(flow.act(people.approver, rid, "reject", reason="Split travel and lodging"))
    flow.ok(flow.resubmit(people.requestor, rid))
    flow.ok(flow.act(people.approver, rid, "approve"))

    resp = flow.client.get(f"/api/requests/{rid}/history", headers=flow.auth(people.requestor))
    assert resp.status_code == 200
    rows = resp.json()
    assert [(r["action"], r["actor_name"]) for r in rows] == [
        ("submitted", "Requestor A"),
        ("rejected", "Approver A"),
        ("resubmitted", "Requestor A"),
        ("approved", "Approver A"),
    ]
    assert (rows[1]["from_status"], rows[1]["to_status"], rows[1]["reason"]) == (
        "PENDING_APPROVAL",
        "RETURNED_FOR_CORRECTION",
        "Split travel and lodging",
    )
    assert rows[0]["from_status"] is None and rows[0]["snapshot"]["amount_requested"] == "30000.00"
    assert set(rows[0]) == {"id", "action", "from_status", "to_status", "reason", "actor_name", "snapshot",
                            "created_at"}


def test_audit_subject_label_for_requests(client, flow, people, auth_header):
    req = flow.raised()
    items = client.get(
        "/api/audit", params={"entity": "fund_requests"}, headers=auth_header(people.finance)
    ).json()["items"]
    assert items[0]["subject_label"] == f"{req['ref_no']} · {flow.project.code}"


# ---------------------------------------------------------------------------
# Lists: mine=action, filters, pagination
# ---------------------------------------------------------------------------


def _ids(flow, user, **params) -> list[int]:
    resp = flow.client.get("/api/requests", params=params, headers=flow.auth(user))
    assert resp.status_code == 200, resp.text
    return [i["id"] for i in resp.json()["items"]]


def test_mine_action_per_role(flow, people, make_request, make_user):
    project, r, a = flow.project, people.requestor, people.approver
    rows = {s: make_request(project, r, a, status=s).id for s in RequestStatus}
    other = make_request(project, people.requestor2, people.approver2, status=S.PENDING_DISBURSEMENT).id

    def mine(user) -> set[int]:
        return set(_ids(flow, user, mine="action", size=100))

    requestor_turn = (S.RETURNED_FOR_CORRECTION, S.PENDING_EXPENSE_SUBMISSION, S.EXPENSE_RETURNED)
    assert mine(r) == {rows[s] for s in requestor_turn}
    assert mine(a) == {rows[S.PENDING_APPROVAL], rows[S.PENDING_EXPENSE_APPROVAL]}
    finance_queue = {rows[S.PENDING_DISBURSEMENT], rows[S.PENDING_VERIFICATION], other}
    assert mine(people.finance) == finance_queue
    assert mine(make_user(Role.SUPER_ADMIN, can_finance_ops=True)) == finance_queue
    assert mine(make_user(Role.SUPER_ADMIN)) == set()
    assert mine(people.approver2) == set()  # `other` is PENDING_DISBURSEMENT: not the approver's turn


def test_list_filters(flow, people, make_project, make_request):
    p1, r, a = flow.project, people.requestor, people.approver
    p2 = make_project(members=(r,))
    x = flow.raised(amount="100", purpose="Water tanks")
    y = flow.raised(amount="200", project=p2, approver=people.approver2)
    z = make_request(p1, people.requestor2, a, status=S.PENDING_DISBURSEMENT).id
    fin = people.finance

    assert _ids(flow, fin) == [z, y["id"], x["id"]]  # newest first
    assert _ids(flow, fin, project_id=p2.id) == [y["id"]]
    assert _ids(flow, fin, status=["PENDING_DISBURSEMENT"]) == [z]
    assert set(_ids(flow, fin, status=["PENDING_DISBURSEMENT", "PENDING_APPROVAL"])) == {x["id"], y["id"], z}
    assert _ids(flow, fin, requestor_id=people.requestor2.id) == [z]
    assert _ids(flow, fin, approver_id=people.approver2.id) == [y["id"]]
    assert _ids(flow, fin, q="water") == [x["id"]]
    assert _ids(flow, fin, q=y["ref_no"].lower()) == [y["id"]]

    today = flow.client.get(f"/api/requests/{x['id']}", headers=flow.auth(fin)).json()["created_at"]
    day = datetime.fromisoformat(today).astimezone(UTC).date()
    assert len(_ids(flow, fin, date_from=str(day), date_to=str(day))) == 3
    assert _ids(flow, fin, date_to=str(day - timedelta(days=1))) == []
    assert _ids(flow, fin, date_from=str(day + timedelta(days=1))) == []

    # Visibility still applies on top of every filter.
    assert set(_ids(flow, r)) == {x["id"], y["id"]}
    assert _ids(flow, people.approver2) == [y["id"]]

    resp = flow.client.get("/api/requests", params={"status": "NOPE"}, headers=flow.auth(fin))
    assert resp.status_code == 400
    resp = flow.client.get("/api/requests", params={"mine": "all"}, headers=flow.auth(fin))
    assert resp.status_code == 400


def test_list_pagination_and_row_shape(flow, people):
    for _ in range(3):
        flow.raised(amount="10")
    resp = flow.client.get("/api/requests", params={"page": 2, "size": 2}, headers=flow.auth(people.finance))
    body = resp.json()
    assert (body["total"], body["page"], body["size"], len(body["items"])) == (3, 2, 2, 1)
    row = body["items"][0]
    assert set(row) == {
        "id", "ref_no", "status", "project", "requestor", "approver",
        "amount_requested", "amount_approved", "created_at", "updated_at",
    }
    assert row["amount_requested"] == "10.00"
    assert set(row["requestor"]) == {"id", "full_name", "email"}


def test_snapshot_fields_are_json_safe(db_session, flow):
    rid = flow.raised()["id"]
    row = db_session.scalar(select(AuditLog).where(AuditLog.entity_id == rid))
    assert set(workflow.SNAPSHOT_FIELDS) <= set(row.snapshot)
