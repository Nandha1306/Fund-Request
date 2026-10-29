"""Invitations, user administration guard rails and the user directories."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.core.enums import RequestStatus, Role
from app.core.errors import BusinessRuleViolation
from app.modules.accounts import service
from app.modules.accounts.models import Invitation
from app.modules.accounts.schemas import UserUpdate
from app.modules.audit.models import AuditLog

INVALID = "Invitation is invalid or has expired"


@pytest.fixture()
def admin(make_user):
    return make_user(Role.SUPER_ADMIN)


def _invite(client, headers, email="new.person@demo.local", role="REQUESTOR"):
    return client.post("/api/users/invite", json={"email": email, "role": role}, headers=headers)


def _token(resp) -> str:
    return resp.json()["invite_link"].split("token=", 1)[1]


def _accept(client, token, password="Secret@123"):
    return client.post(
        "/api/auth/accept-invite", json={"token": token, "full_name": "New Person", "password": password}
    )


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def test_invite_accept_and_log_in(client, admin, auth_header):
    resp = _invite(client, auth_header(admin), email="  New.Person@Demo.local ")
    assert resp.status_code == 201
    assert resp.json()["email"] == "new.person@demo.local"
    assert "/accept-invite?token=" in resp.json()["invite_link"]

    accepted = _accept(client, _token(resp))
    assert accepted.status_code == 201
    me = client.get("/api/me", headers={"Authorization": f"Bearer {accepted.json()['access_token']}"})
    assert (me.status_code, me.json()["role"]) == (200, "REQUESTOR")


def test_used_expired_and_unknown_tokens_get_the_same_400(client, db_session, admin, auth_header):
    headers = auth_header(admin)
    used = _token(_invite(client, headers, email="used@demo.local"))
    assert _accept(client, used).status_code == 201

    expired = _token(_invite(client, headers, email="late@demo.local"))
    db_session.execute(
        update(Invitation)
        .where(Invitation.email == "late@demo.local")
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )

    for token in (used, expired, "not-a-real-token"):
        resp = _accept(client, token)
        assert resp.status_code == 400
        assert resp.json()["error"]["message"] == INVALID


def test_reinvite_invalidates_the_old_link(client, admin, auth_header):
    headers = auth_header(admin)
    first = _token(_invite(client, headers))
    second = _token(_invite(client, headers))

    assert _accept(client, first).status_code == 400
    assert _accept(client, second).status_code == 201


@pytest.mark.parametrize("is_active", [True, False])
def test_invite_existing_user_email_is_409(client, admin, make_user, auth_header, is_active):
    existing = make_user(Role.APPROVER, is_active=is_active)
    assert _invite(client, auth_header(admin), email=existing.email).status_code == 409


def test_only_super_admin_can_invite(client, make_user, auth_header):
    assert _invite(client, auth_header(make_user(Role.FINANCE))).status_code == 403


def test_token_is_never_written_to_the_audit_log(client, db_session, admin, auth_header):
    token = _token(_invite(client, auth_header(admin)))
    snapshots = db_session.scalars(select(AuditLog.snapshot).where(AuditLog.entity == "invitations")).all()
    assert snapshots and all(token not in str(s) for s in snapshots)
    assert all(token not in inv.token_hash for inv in db_session.scalars(select(Invitation)))


def test_pending_invitations_list_has_no_links(client, admin, auth_header):
    headers = auth_header(admin)
    _invite(client, headers, email="p1@demo.local")
    _accept(client, _token(_invite(client, headers, email="p2@demo.local")))

    listed = client.get("/api/users/invitations", headers=headers).json()
    assert [i["email"] for i in listed] == ["p1@demo.local"]
    assert "invite_link" not in listed[0]


def test_accept_requires_a_proper_password(client, admin, auth_header):
    token = _token(_invite(client, auth_header(admin)))
    assert _accept(client, token, password="short").status_code == 400
    assert _accept(client, token, password="x" * 73).status_code == 400


# ---------------------------------------------------------------------------
# PATCH /users/{id} guard rails
# ---------------------------------------------------------------------------


def _patch(client, headers, user_id, body):
    return client.patch(f"/api/users/{user_id}", json=body, headers=headers)


def test_cannot_change_own_role_or_deactivate_self(client, admin, auth_header):
    headers = auth_header(admin)
    assert _patch(client, headers, admin.id, {"is_active": False}).status_code == 422
    assert _patch(client, headers, admin.id, {"role": "FINANCE"}).status_code == 422


def test_last_active_super_admin_is_protected(db_session, admin, make_user):
    # Unreachable through the API (an admin cannot target themselves), so test the service guard directly.
    other = make_user(Role.FINANCE)
    with pytest.raises(BusinessRuleViolation, match="At least one active Super Admin"):
        service.update_user(db_session, other, admin.id, UserUpdate(is_active=False))


def test_can_finance_ops_only_for_super_admin(client, admin, make_user, auth_header):
    finance = make_user(Role.FINANCE)
    resp = _patch(client, auth_header(admin), finance.id, {"can_finance_ops": True})
    assert resp.status_code == 422

    second_admin = make_user(Role.SUPER_ADMIN)
    granted = _patch(client, auth_header(admin), second_admin.id, {"can_finance_ops": True}).json()["user"]
    assert granted["can_finance_ops"] is True and "request.disburse" in granted["permissions"]


def test_leaving_super_admin_resets_finance_ops(client, admin, make_user, auth_header):
    second_admin = make_user(Role.SUPER_ADMIN, can_finance_ops=True)
    user = _patch(client, auth_header(admin), second_admin.id, {"role": "FINANCE"}).json()["user"]
    assert (user["role"], user["can_finance_ops"]) == ("FINANCE", False)


def test_role_change_blocked_by_an_open_request(
    client, admin, make_user, make_project, make_request, auth_header
):
    req, approver = make_user(Role.REQUESTOR), make_user(Role.APPROVER)
    request = make_request(make_project(), req, approver)

    resp = _patch(client, auth_header(admin), req.id, {"role": "APPROVER"})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["request_refs"] == [request.ref_no]


def test_role_change_allowed_once_requests_are_terminal(
    client, admin, make_user, make_project, make_request, auth_header
):
    req = make_user(Role.REQUESTOR)
    make_request(make_project(), req, make_user(Role.APPROVER), status=RequestStatus.LIQUIDATED)
    assert _patch(client, auth_header(admin), req.id, {"role": "APPROVER"}).status_code == 200


def test_approver_with_stage_5_work_cannot_be_deactivated(
    client, admin, make_user, make_project, make_request, auth_header
):
    approver = make_user(Role.APPROVER)
    request = make_request(
        make_project(), make_user(Role.REQUESTOR), approver, status=RequestStatus.PENDING_EXPENSE_APPROVAL
    )
    resp = _patch(client, auth_header(admin), approver.id, {"is_active": False})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["request_refs"] == [request.ref_no]


def test_deactivating_approver_reports_requests_needing_reassignment(
    client, db_session, admin, make_user, make_project, make_request, auth_header
):
    approver = make_user(Role.APPROVER)
    request = make_request(make_project(), make_user(Role.REQUESTOR), approver)

    resp = _patch(client, auth_header(admin), approver.id, {"is_active": False})
    assert resp.status_code == 200
    assert resp.json()["user"]["is_active"] is False
    assert resp.json()["needs_reassignment"] == [request.ref_no]

    row = db_session.scalar(
        select(AuditLog).where(AuditLog.entity == "users", AuditLog.entity_id == approver.id)
    )
    assert row.snapshot["before"]["is_active"] is True and row.snapshot["after"]["is_active"] is False


def test_no_op_patch_writes_no_audit_row(client, db_session, admin, make_user, auth_header):
    user = make_user(Role.REQUESTOR)
    assert _patch(client, auth_header(admin), user.id, {"is_active": True}).status_code == 200
    assert (
        db_session.scalar(select(AuditLog).where(AuditLog.entity == "users", AuditLog.entity_id == user.id))
        is None
    )


def test_patch_rejects_null_and_unknown_user(client, admin, auth_header):
    headers = auth_header(admin)
    assert _patch(client, headers, admin.id, {"role": None}).status_code == 400
    assert _patch(client, headers, 999999, {"is_active": True}).status_code == 404


# ---------------------------------------------------------------------------
# Lists and directories
# ---------------------------------------------------------------------------


def test_list_users_is_admin_only_and_filters(client, admin, make_user, auth_header):
    make_user(Role.APPROVER, full_name="Priya Approver")
    make_user(Role.APPROVER, is_active=False)
    assert client.get("/api/users", headers=auth_header(make_user(Role.FINANCE))).status_code == 403

    body = client.get(
        "/api/users", params={"role": "APPROVER", "is_active": True}, headers=auth_header(admin)
    ).json()
    assert body["total"] == 1 and body["items"][0]["full_name"] == "Priya Approver"
    assert client.get("/api/users", params={"q": "priya"}, headers=auth_header(admin)).json()["total"] == 1


def test_approvers_directory_excludes_caller_and_inactive(client, make_user, auth_header):
    me, other = make_user(Role.APPROVER), make_user(Role.APPROVER)
    make_user(Role.APPROVER, is_active=False)

    ids = [u["id"] for u in client.get("/api/users/approvers", headers=auth_header(me)).json()]
    assert ids == [other.id]


def test_requestors_directory_needs_project_members_permission(client, make_user, auth_header):
    req = make_user(Role.REQUESTOR)
    assert client.get("/api/users/requestors", headers=auth_header(req)).status_code == 403

    ids = [
        u["id"]
        for u in client.get("/api/users/requestors", headers=auth_header(make_user(Role.FINANCE))).json()
    ]
    assert ids == [req.id]
