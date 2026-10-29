"""The transition table (phase 3 §2 and phase 4 §2), exhaustively: every (status, action) pair either reaches
its target or is a 409, and ``allowed_actions`` offers exactly what the API would accept, per role."""

from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.enums import ApprovalStatus, RequestStatus, Role, VerificationStatus
from app.core.errors import Conflict, Forbidden
from app.modules.expenses.models import Document, ExpenseItem
from app.modules.finance import budget
from app.modules.finance.models import Disbursement
from app.modules.fund_requests import workflow
from app.modules.fund_requests.workflow import TRANSITIONS, Action

S = RequestStatus

ALLOWED: dict[tuple[RequestStatus, str], RequestStatus] = {
    (S.RETURNED_FOR_CORRECTION, "resubmit"): S.PENDING_APPROVAL,
    (S.RETURNED_FOR_CORRECTION, "withdraw"): S.WITHDRAWN,
    (S.PENDING_APPROVAL, "approve"): S.PENDING_DISBURSEMENT,
    (S.PENDING_APPROVAL, "reject"): S.RETURNED_FOR_CORRECTION,
    (S.PENDING_APPROVAL, "reassign"): S.PENDING_APPROVAL,
    (S.PENDING_DISBURSEMENT, "disburse"): S.PENDING_EXPENSE_SUBMISSION,
    (S.PENDING_DISBURSEMENT, "return"): S.RETURNED_FOR_CORRECTION,
    (S.PENDING_EXPENSE_SUBMISSION, "return"): S.RETURNED_FOR_CORRECTION,
    # Phase 4 §2
    (S.PENDING_EXPENSE_SUBMISSION, "save_expenses"): S.PENDING_EXPENSE_SUBMISSION,
    (S.EXPENSE_RETURNED, "save_expenses"): S.EXPENSE_RETURNED,
    (S.PENDING_EXPENSE_SUBMISSION, "submit_expenses"): S.PENDING_EXPENSE_APPROVAL,
    (S.EXPENSE_RETURNED, "submit_expenses"): S.PENDING_EXPENSE_APPROVAL,
    (S.PENDING_EXPENSE_APPROVAL, "review_item"): S.PENDING_EXPENSE_APPROVAL,
    (S.PENDING_EXPENSE_APPROVAL, "send_back_expenses"): S.EXPENSE_RETURNED,
    (S.PENDING_EXPENSE_APPROVAL, "approve_expenses"): S.PENDING_VERIFICATION,
    (S.PENDING_VERIFICATION, "verify_item"): S.PENDING_VERIFICATION,
    (S.PENDING_VERIFICATION, "return_expenses"): S.EXPENSE_RETURNED,
    (S.PENDING_VERIFICATION, "liquidate"): S.LIQUIDATED,
}
ACTIONS = ["resubmit", "withdraw", "approve", "reject", "reassign", "disburse", "return"]
EXPENSE_ACTIONS = [
    "save_expenses", "submit_expenses", "review_item", "send_back_expenses", "approve_expenses",
    "verify_item", "return_expenses", "liquidate",
]

A, V = ApprovalStatus, VerificationStatus
PAID = (S.PENDING_EXPENSE_SUBMISSION, S.PENDING_EXPENSE_APPROVAL, S.EXPENSE_RETURNED, S.PENDING_VERIFICATION,
        S.LIQUIDATED)
# The one expense item a request in this status carries: (approval, verification).
ITEM_STATE = {
    S.PENDING_EXPENSE_SUBMISSION: (A.PENDING, V.PENDING),
    S.EXPENSE_RETURNED: (A.REJECTED, V.PENDING),
    S.PENDING_EXPENSE_APPROVAL: (A.PENDING, V.PENDING),
    S.PENDING_VERIFICATION: (A.APPROVED, V.PENDING),
    S.LIQUIDATED: (A.APPROVED, V.VERIFIED),
}


@pytest.fixture()
def in_status(db_session, people, flow, make_request):
    """A 1,000.00 request placed directly in *status*, with consistent money."""

    def _factory(status: RequestStatus):
        request = make_request(
            flow.project, people.requestor, people.approver, status=status, amount="1000.00"
        )
        if status not in (S.PENDING_APPROVAL, S.RETURNED_FOR_CORRECTION, S.WITHDRAWN):
            request.amount_approved = D("1000.00")
        if status in (S.PENDING_APPROVAL, S.PENDING_DISBURSEMENT):
            budget.reserve(db_session, flow.project, request, D("1000.00"), actor_id=None)
        if status in PAID:  # paid out in full: the money is outstanding (and liquidated at the end)
            budget.reserve(db_session, flow.project, request, D("1000.00"), actor_id=None)
            budget.disburse(db_session, flow.project, request, D("1000.00"), actor_id=None)
            db_session.add(
                Disbursement(request_id=request.id, amount=D("1000.00"), txn_ref=f"T-{uuid4().hex[:8]}",
                             disbursed_on=date.today(), idempotency_key=uuid4().hex,
                             created_by=people.finance.id)
            )
            if status == S.LIQUIDATED:
                paid = D("1000.00")
                budget.settle(db_session, flow.project, request, verified=paid, disbursed=paid, actor_id=None)
        if status in ITEM_STATE:
            approval, verification = ITEM_STATE[status]
            item = ExpenseItem(request_id=request.id, description="Taxi", amount=D("1000.00"),
                               expense_date=date.today(), approval_status=approval.value,
                               verification_status=verification.value)
            db_session.add(item)
            db_session.flush()
            db_session.add(Document(request_id=request.id, expense_item_id=item.id, storage_key=uuid4().hex,
                                    original_name="taxi.pdf", mime_type="application/pdf", size_bytes=10,
                                    sha256="0" * 64, uploaded_by=people.requestor.id))
        db_session.flush()
        return request

    return _factory


def _call(flow, people, request_id: int, action: str):
    if action == "resubmit":
        return flow.resubmit(people.requestor, request_id, amount="1000.00")
    if action == "withdraw":
        return flow.act(people.requestor, request_id, "withdraw")
    if action == "approve":
        return flow.act(people.approver, request_id, "approve")
    if action == "reject":
        return flow.act(people.approver, request_id, "reject", reason="Needs a quote")
    if action == "reassign":
        return flow.act(people.finance, request_id, "reassign", approver_id=people.approver2.id)
    if action == "disburse":
        return flow.disburse(request_id)
    return flow.act(people.finance, request_id, "return", reason="Wrong cost centre")


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("status", list(RequestStatus))
def test_every_status_action_pair(db_session, flow, people, in_status, status, action):
    request = in_status(status)
    version = request.version
    resp = _call(flow, people, request.id, action)

    if (status, action) in ALLOWED:
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == ALLOWED[(status, action)].value
        assert body["version"] == version + 1
    else:
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["details"] == {"status": status.value, "action": action}
    assert budget.reconcile(db_session, flow.project.id) == budget.buckets_of(flow.project)


# Item state each legal phase 4 action needs, set just before the call.
_PREPARE = {
    "send_back_expenses": {"approval_status": A.REJECTED.value},
    "approve_expenses": {"approval_status": A.APPROVED.value},
    "return_expenses": {"verification_status": V.RETURNED.value},
    "liquidate": {"verification_status": V.VERIFIED.value},
}


def _call_expense_action(flow, request_id: int, item_id: int | None, action: str):
    if action == "save_expenses":
        return flow.save(request_id, [])
    if action == "submit_expenses":
        return flow.submit(request_id)
    if action == "review_item":
        return flow.review(request_id, item_id or 999999)
    if action == "send_back_expenses":
        return flow.send_back(request_id)
    if action == "approve_expenses":
        return flow.approve_expenses(request_id)
    if action == "verify_item":
        return flow.verify(request_id, item_id or 999999)
    if action == "return_expenses":
        return flow.return_expenses(request_id)
    return flow.liquidate(request_id)


@pytest.mark.parametrize("action", EXPENSE_ACTIONS)
@pytest.mark.parametrize("status", list(RequestStatus))
def test_every_status_expense_action_pair(db_session, flow, people, in_status, status, action):
    request = in_status(status)
    version = request.version
    item = db_session.query(ExpenseItem).filter_by(request_id=request.id).one_or_none()
    if (status, action) in ALLOWED and action in _PREPARE:
        for field, value in _PREPARE[action].items():
            setattr(item, field, value)
        db_session.flush()
    resp = _call_expense_action(flow, request.id, item.id if item else None, action)

    if (status, action) in ALLOWED:
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == ALLOWED[(status, action)].value
        assert body["version"] == version + 1
    else:
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["details"] == {"status": status.value, "action": action}
    assert budget.reconcile(db_session, flow.project.id) == budget.buckets_of(flow.project)


def test_table_matches_the_phase_document():
    table = {(k[0], k[1].value): v.to for k, v in TRANSITIONS.items() if k[0] is not None}
    assert table == ALLOWED
    assert TRANSITIONS[(None, Action.SUBMIT)].to == S.PENDING_APPROVAL
    assert TRANSITIONS[(S.PENDING_APPROVAL, Action.APPROVE)].targets == (
        S.PENDING_DISBURSEMENT,
        S.PENDING_EXPENSE_SUBMISSION,
    )


# ---------------------------------------------------------------------------
# allowed_actions, per role
# ---------------------------------------------------------------------------

EXPECTED_ACTIONS: dict[RequestStatus, dict[str, list[str]]] = {
    S.PENDING_APPROVAL: {"approver": ["approve", "reject"], "finance": ["reassign"], "admin": ["reassign"],
                         "admin_ops": ["reassign"]},
    S.RETURNED_FOR_CORRECTION: {"requestor": ["resubmit", "withdraw"]},
    S.PENDING_DISBURSEMENT: {"finance": ["disburse", "return"], "admin_ops": ["disburse", "return"]},
    S.PENDING_EXPENSE_SUBMISSION: {"requestor": ["edit_expenses", "submit_expenses"], "finance": ["return"],
                                   "admin_ops": ["return"]},
    S.EXPENSE_RETURNED: {"requestor": ["edit_expenses", "submit_expenses"]},
    S.PENDING_EXPENSE_APPROVAL: {"approver": ["review_items"]},
    S.PENDING_VERIFICATION: {"finance": ["verify_items"], "admin_ops": ["verify_items"]},
}


@pytest.mark.parametrize("status", list(RequestStatus))
def test_allowed_actions_per_role(flow, people, make_user, in_status, status):
    request = in_status(status)
    users = {
        "requestor": people.requestor,
        "approver": people.approver,
        "finance": people.finance,
        "admin": make_user(Role.SUPER_ADMIN),
        "admin_ops": make_user(Role.SUPER_ADMIN, can_finance_ops=True),
    }
    expected = EXPECTED_ACTIONS.get(status, {})
    for name, user in users.items():
        body = flow.ok(flow.get(user, request.id))
        assert body["allowed_actions"] == expected.get(name, []), (status, name)


def test_withdraw_is_not_offered_after_a_payout(db_session, flow, people, in_status):
    request = in_status(S.RETURNED_FOR_CORRECTION)
    db_session.add(
        Disbursement(
            request_id=request.id,
            amount=D("1000.00"),
            txn_ref=f"T-{uuid4().hex[:8]}",
            disbursed_on=date.today(),
            idempotency_key=uuid4().hex,
            created_by=people.finance.id,
        )
    )
    db_session.flush()
    assert flow.ok(flow.get(people.requestor, request.id))["allowed_actions"] == ["resubmit"]


def test_allowed_actions_is_pure_and_uses_the_object_rule():
    requestor = SimpleNamespace(id=1, role=Role.REQUESTOR.value, can_finance_ops=False)
    other_approver = SimpleNamespace(id=9, role=Role.APPROVER.value, can_finance_ops=False)
    request = SimpleNamespace(requestor_id=1, approver_id=2, status=S.PENDING_APPROVAL.value)
    assert workflow.allowed_actions(other_approver, request, D("0")) == []
    assert workflow.allowed_actions(requestor, request, D("0")) == []


def _items(*states: tuple[str, str]) -> list[SimpleNamespace]:
    return [SimpleNamespace(approval_status=a, verification_status=v) for a, v in states]


@pytest.mark.parametrize(
    ("status", "role", "states", "expected"),
    [
        (S.PENDING_EXPENSE_SUBMISSION, "requestor", [], ["edit_expenses"]),
        (S.EXPENSE_RETURNED, "requestor", [("APPROVED", "PENDING")], ["edit_expenses", "submit_expenses"]),
        (S.PENDING_EXPENSE_APPROVAL, "approver", [("APPROVED", "PENDING"), ("PENDING", "PENDING")],
         ["review_items"]),
        (S.PENDING_EXPENSE_APPROVAL, "approver", [("APPROVED", "PENDING"), ("REJECTED", "PENDING")],
         ["review_items", "send_back_expenses"]),
        (S.PENDING_EXPENSE_APPROVAL, "approver", [("APPROVED", "PENDING"), ("APPROVED", "VERIFIED")],
         ["approve_expenses"]),
        (S.PENDING_EXPENSE_APPROVAL, "approver", [], []),
        (S.PENDING_EXPENSE_APPROVAL, "other_approver", [("PENDING", "PENDING")], []),
        (S.PENDING_VERIFICATION, "finance", [("APPROVED", "VERIFIED"), ("APPROVED", "PENDING")],
         ["verify_items"]),
        (S.PENDING_VERIFICATION, "finance", [("APPROVED", "VERIFIED"), ("APPROVED", "RETURNED")],
         ["verify_items", "return_expenses"]),
        (S.PENDING_VERIFICATION, "finance", [("APPROVED", "VERIFIED"), ("APPROVED", "VERIFIED")],
         ["liquidate"]),
        (S.PENDING_VERIFICATION, "finance", [], []),
        (S.PENDING_VERIFICATION, "approver", [("APPROVED", "VERIFIED")], []),
    ],
)
def test_allowed_actions_follow_the_item_rules(status, role, states, expected):
    users = {
        "requestor": SimpleNamespace(id=1, role=Role.REQUESTOR.value, can_finance_ops=False),
        "approver": SimpleNamespace(id=2, role=Role.APPROVER.value, can_finance_ops=False),
        "other_approver": SimpleNamespace(id=9, role=Role.APPROVER.value, can_finance_ops=False),
        "finance": SimpleNamespace(id=3, role=Role.FINANCE.value, can_finance_ops=False),
    }
    request = SimpleNamespace(requestor_id=1, approver_id=2, status=status.value)
    assert workflow.allowed_actions(users[role], request, D("1000"), _items(*states)) == expected


def test_item_editability():
    states = [
        ("PENDING", "PENDING"), ("REJECTED", "PENDING"), ("APPROVED", "PENDING"), ("PENDING", "RETURNED"),
        ("APPROVED", "VERIFIED"),
    ]
    assert [workflow.is_item_editable(i) for i in _items(*states)] == [True, True, False, True, False]


# ---------------------------------------------------------------------------
# workflow.apply / check guards
# ---------------------------------------------------------------------------

def test_apply_refuses_a_stale_version_and_an_illegal_target(db_session, in_status, people):
    request = in_status(S.PENDING_APPROVAL)
    with pytest.raises(Conflict) as exc:
        workflow.apply(db_session, request, Action.APPROVE, actor_id=people.approver.id, expected_version=99)
    assert exc.value.message == workflow.STALE_VERSION
    with pytest.raises(ValueError):
        workflow.apply(
            db_session, request, Action.APPROVE, S.WITHDRAWN, actor_id=people.approver.id, expected_version=1
        )
    assert request.status == S.PENDING_APPROVAL.value and request.version == 1


def test_apply_moves_status_bumps_version_and_audits(db_session, in_status, people):
    request = in_status(S.PENDING_APPROVAL)
    row = workflow.apply(
        db_session, request, Action.REJECT, actor_id=people.approver.id, expected_version=1, reason="No"
    )
    assert (request.status, request.version) == (S.RETURNED_FOR_CORRECTION.value, 2)
    assert (row.entity, row.action, row.from_status, row.to_status, row.reason) == (
        "fund_requests",
        "rejected",
        "PENDING_APPROVAL",
        "RETURNED_FOR_CORRECTION",
        "No",
    )
    assert row.snapshot["status"] == "RETURNED_FOR_CORRECTION" and row.snapshot["version"] == 2


def test_object_rule_is_403(in_status, people):
    request = in_status(S.PENDING_APPROVAL)
    with pytest.raises(Forbidden):
        workflow.ensure_party(people.approver2, request, Action.APPROVE)
    with pytest.raises(Forbidden):
        workflow.ensure_party(people.requestor2, request, Action.WITHDRAW)
    workflow.ensure_party(people.finance, request, Action.REASSIGN)  # permission-only action
