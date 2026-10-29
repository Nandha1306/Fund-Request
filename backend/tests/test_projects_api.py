"""Project API: CRUD, role-filtered visibility (404, not 403), members and the ledger view."""

from decimal import Decimal as D

import pytest
from sqlalchemy import func, select

from app.core.enums import ProjectStatus, Role
from app.modules.audit.models import AuditLog
from app.modules.finance import budget
from app.modules.finance.models import BudgetLedger
from app.modules.projects.models import Project


@pytest.fixture()
def finance(make_user):
    return make_user(Role.FINANCE)


def _audit_actions(db, project_id) -> list[str]:
    return list(
        db.scalars(
            select(AuditLog.action)
            .where(AuditLog.entity == "projects", AuditLog.entity_id == project_id)
            .order_by(AuditLog.id)
        )
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_writes_exactly_one_allocate_row_and_an_audit_row(client, db_session, finance, auth_header):
    resp = client.post(
        "/api/projects",
        json={"code": "prj-104", "name": "Water Pilot", "allocated": "50000"},
        headers=auth_header(finance),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == "PRJ-104"  # normalised to upper case
    assert D(body["allocated"]) == D("50000") and D(body["available"]) == D("50000")

    rows = budget.ledger_rows(db_session, body["id"])
    assert [(r.entry_type, r.amount) for r in rows] == [("ALLOCATE", D("50000.00"))]
    assert _audit_actions(db_session, body["id"]) == ["created"]
    project = db_session.get(Project, body["id"])
    assert budget.reconcile(db_session, project.id) == budget.buckets_of(project)


@pytest.mark.parametrize("code", ["ab", "PRJ 1", "PRJ_1", "X" * 21])
def test_create_rejects_invalid_codes(client, finance, auth_header, code):
    resp = client.post(
        "/api/projects", json={"code": code, "name": "n", "allocated": "1"}, headers=auth_header(finance)
    )
    assert resp.status_code == 400


def test_create_duplicate_code_is_409(client, finance, make_project, auth_header):
    make_project(code="PRJ-200")
    resp = client.post(
        "/api/projects", json={"code": "prj-200", "name": "n", "allocated": "1"}, headers=auth_header(finance)
    )
    assert resp.status_code == 409


@pytest.mark.parametrize("role", [Role.REQUESTOR, Role.APPROVER])
def test_only_project_managers_can_create(client, make_user, auth_header, role):
    resp = client.post(
        "/api/projects",
        json={"code": "PRJ-300", "name": "n", "allocated": "1"},
        headers=auth_header(make_user(role)),
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def test_requestor_sees_only_active_projects_they_belong_to(client, make_user, make_project, auth_header):
    req = make_user(Role.REQUESTOR)
    mine = make_project(code="PRJ-401", members=(req,))
    not_member = make_project(code="PRJ-402")
    inactive = make_project(code="PRJ-403", status=ProjectStatus.INACTIVE, members=(req,))
    headers = auth_header(req)

    listed = client.get("/api/projects", headers=headers).json()
    assert [p["code"] for p in listed["items"]] == ["PRJ-401"]
    assert client.get(f"/api/projects/{mine.id}", headers=headers).status_code == 200
    assert client.get(f"/api/projects/{not_member.id}", headers=headers).status_code == 404
    assert client.get(f"/api/projects/{inactive.id}", headers=headers).status_code == 404


def test_approver_sees_a_project_once_they_approve_a_request_on_it(
    client, make_user, make_project, make_request, auth_header
):
    approver, req = make_user(Role.APPROVER), make_user(Role.REQUESTOR)
    project = make_project(code="PRJ-501", members=(req,))
    headers = auth_header(approver)

    assert client.get(f"/api/projects/{project.id}", headers=headers).status_code == 404
    make_request(project, req, approver)
    assert client.get(f"/api/projects/{project.id}", headers=headers).status_code == 200
    assert client.get("/api/projects", headers=headers).json()["total"] == 1


def test_finance_list_filters_and_paginates(client, finance, make_project, auth_header):
    for code in ("PRJ-601", "PRJ-602", "OTH-603"):
        make_project(code=code)
    make_project(code="PRJ-604", status=ProjectStatus.INACTIVE)
    headers = auth_header(finance)

    page = client.get("/api/projects", params={"q": "prj-60", "size": 2}, headers=headers).json()
    assert (page["total"], [p["code"] for p in page["items"]]) == (3, ["PRJ-601", "PRJ-602"])
    inactive = client.get("/api/projects", params={"status": "INACTIVE"}, headers=headers).json()
    assert [p["code"] for p in inactive["items"]] == ["PRJ-604"]


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def test_allocation_cannot_drop_below_commitments(
    client, db_session, finance, make_user, make_project, make_request, auth_header
):
    project = make_project(allocated="1000.00")
    request = make_request(project, make_user(Role.REQUESTOR), make_user(Role.APPROVER), amount="800")
    budget.reserve(db_session, project, request, D("800"), actor_id=None)
    headers = auth_header(finance)

    resp = client.patch(f"/api/projects/{project.id}", json={"allocated": "799.99"}, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["details"] == {"committed": "800.00"}

    resp = client.patch(f"/api/projects/{project.id}", json={"allocated": "900"}, headers=headers)
    assert resp.status_code == 200
    assert D(resp.json()["available"]) == D("100.00")
    allocations = [r.amount for r in budget.ledger_rows(db_session, project.id) if r.entry_type == "ALLOCATE"]
    assert allocations == [D("1000.00"), D("-100.00")]
    assert budget.reconcile(db_session, project.id) == budget.buckets_of(project)


def test_empty_patch_changes_nothing_and_null_description_clears(
    client, db_session, finance, make_project, auth_header
):
    project = make_project()
    headers = auth_header(finance)

    assert client.patch(f"/api/projects/{project.id}", json={}, headers=headers).status_code == 200
    assert _audit_actions(db_session, project.id) == []

    client.patch(f"/api/projects/{project.id}", json={"description": "Phase A"}, headers=headers)
    resp = client.patch(f"/api/projects/{project.id}", json={"description": None}, headers=headers)
    assert resp.json()["description"] is None
    assert _audit_actions(db_session, project.id) == ["updated", "updated"]

    assert (
        client.patch(f"/api/projects/{project.id}", json={"name": None}, headers=headers).status_code == 400
    )


def test_deactivation_is_audited_and_keeps_reservations(
    client, db_session, finance, make_user, make_project, make_request, auth_header
):
    project = make_project()
    request = make_request(project, make_user(Role.REQUESTOR), make_user(Role.APPROVER))
    budget.reserve(db_session, project, request, D("1000"), actor_id=None)

    resp = client.patch(
        f"/api/projects/{project.id}", json={"status": "INACTIVE"}, headers=auth_header(finance)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "INACTIVE"
    assert D(resp.json()["reserved"]) == D("1000.00")
    deactivated = db_session.scalar(
        select(AuditLog).where(AuditLog.entity_id == project.id, AuditLog.action == "deactivated")
    )
    assert (deactivated.from_status, deactivated.to_status) == ("ACTIVE", "INACTIVE")


def test_patch_unknown_project_is_404(client, finance, auth_header):
    assert client.patch("/api/projects/999999", json={}, headers=auth_header(finance)).status_code == 404


# ---------------------------------------------------------------------------
# Members and ledger
# ---------------------------------------------------------------------------


def test_members_must_be_active_requestors(client, finance, make_user, make_project, auth_header):
    project = make_project()
    approver = make_user(Role.APPROVER)
    inactive = make_user(Role.REQUESTOR, is_active=False)

    resp = client.put(
        f"/api/projects/{project.id}/members",
        json={"user_ids": [approver.id, inactive.id, 999999]},
        headers=auth_header(finance),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["invalid_user_ids"] == sorted([approver.id, inactive.id, 999999])


def test_members_replace_is_audited_with_added_and_removed(
    client, db_session, finance, make_user, make_project, auth_header
):
    a, b, c = (make_user(Role.REQUESTOR) for _ in range(3))
    project = make_project(members=(a, b))
    headers = auth_header(finance)

    resp = client.put(
        f"/api/projects/{project.id}/members", json={"user_ids": [b.id, c.id, c.id]}, headers=headers
    )
    assert resp.status_code == 200
    assert sorted(m["user_id"] for m in resp.json()) == sorted([b.id, c.id])

    row = db_session.scalar(
        select(AuditLog).where(AuditLog.entity_id == project.id, AuditLog.action == "members_changed")
    )
    assert row.snapshot == {
        "added": [c.id],
        "removed": [a.id],
        "added_names": [c.full_name],
        "removed_names": [a.full_name],
    }
    listed = client.get(f"/api/projects/{project.id}/members", headers=headers).json()
    assert sorted(m["user_id"] for m in listed) == sorted([b.id, c.id])


def test_members_of_unknown_project_is_404(client, finance, auth_header):
    assert client.get("/api/projects/999999/members", headers=auth_header(finance)).status_code == 404


def test_ledger_view_is_for_project_managers_only(
    client, db_session, finance, make_user, make_project, auth_header
):
    req = make_user(Role.REQUESTOR)
    project = make_project(members=(req,))
    assert client.get(f"/api/projects/{project.id}/ledger", headers=auth_header(req)).status_code == 403

    body = client.get(f"/api/projects/{project.id}/ledger", headers=auth_header(finance)).json()
    assert body["total"] == db_session.scalar(
        select(func.count()).where(BudgetLedger.project_id == project.id)
    )
    assert body["items"][0]["entry_type"] == "ALLOCATE"
    assert body["items"][0]["actor_name"] == "Test User"  # make_project's creator, a make_user default
    assert body["items"][0]["request_ref"] is None  # allocation rows belong to no request
