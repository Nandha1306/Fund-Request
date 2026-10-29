"""D12: an invisible request is a 404 (never a 403), everywhere a request id is accepted."""

import pytest

from app.core.enums import Role


@pytest.fixture()
def request_id(flow) -> int:
    return flow.raised()["id"]


def test_other_requestor_gets_404(flow, people, request_id):
    assert flow.get(people.requestor2, request_id).status_code == 404
    assert request_id not in [i["id"] for i in flow.client.get(
        "/api/requests", headers=flow.auth(people.requestor2)
    ).json()["items"]]
    resp = flow.resubmit(people.requestor2, request_id, version=1)
    assert resp.status_code == 404
    assert flow.act(people.requestor2, request_id, "withdraw", version=1).status_code == 404


def test_other_approver_gets_404_until_reassigned(flow, people, request_id):
    assert flow.get(people.approver2, request_id).status_code == 404
    assert flow.act(people.approver2, request_id, "approve", version=1).status_code == 404
    assert flow.act(people.approver2, request_id, "reject", version=1, reason="x").status_code == 404

    flow.ok(flow.act(people.finance, request_id, "reassign", approver_id=people.approver2.id))
    assert flow.get(people.approver2, request_id).status_code == 200
    assert flow.get(people.approver, request_id).status_code == 404


@pytest.mark.parametrize("ops", [False, True])
def test_finance_and_super_admin_see_everything(flow, people, make_user, request_id, ops):
    admin = make_user(Role.SUPER_ADMIN, can_finance_ops=ops)
    for user in (people.finance, admin):
        assert flow.get(user, request_id).status_code == 200
        items = flow.client.get("/api/requests", headers=flow.auth(user)).json()["items"]
        assert [i["id"] for i in items] == [request_id]


def test_history_is_404_when_invisible(flow, people, request_id):
    url = f"/api/requests/{request_id}/history"
    assert flow.client.get(url, headers=flow.auth(people.requestor2)).status_code == 404
    assert flow.client.get(url, headers=flow.auth(people.approver2)).status_code == 404
    assert flow.client.get(url, headers=flow.auth(people.approver)).status_code == 200


def test_missing_request_is_404(flow, people):
    assert flow.get(people.finance, 999_999).status_code == 404
    resp = flow.client.get("/api/requests/999999/history", headers=flow.auth(people.finance))
    assert resp.status_code == 404
    assert flow.act(people.finance, 999_999, "return", version=1, reason="x").status_code == 404


def test_requires_authentication(client):
    assert client.get("/api/requests").status_code == 401
    assert client.get("/api/requests/1").status_code == 401
