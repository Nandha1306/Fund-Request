"""Audit/notification writers and the audit log API."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from app.core.enums import ProjectStatus, Role
from app.modules.audit import service as audit
from app.modules.audit.models import AuditLog, Notification


def test_writers_never_commit(db_session, make_user, monkeypatch):
    user = make_user(Role.FINANCE)

    def _no_commit():
        raise AssertionError("audit helpers must not commit")

    monkeypatch.setattr(db_session, "commit", _no_commit)
    row = audit.record(db_session, entity="projects", entity_id=1, action="created", actor_id=user.id)
    audit.notify(db_session, user_ids=[user.id], type="test", message="hello")

    assert db_session.get(AuditLog, row.id) is row  # flushed, visible in the same transaction
    assert db_session.scalar(select(Notification).where(Notification.user_id == user.id)).message == "hello"


def test_notify_deduplicates_users(db_session, make_user):
    a, b = make_user(Role.REQUESTOR), make_user(Role.APPROVER)
    audit.notify(db_session, user_ids=[a.id, b.id, a.id], type="t", message="m")
    rows = db_session.scalars(select(Notification).where(Notification.type == "t")).all()
    assert sorted(r.user_id for r in rows) == sorted([a.id, b.id])


def test_snapshot_is_json_safe_but_keeps_plain_types():
    obj = SimpleNamespace(
        amount=Decimal("10.50"),
        on=date(2026, 10, 29),
        at=datetime(2026, 10, 29, 9, 30, tzinfo=UTC),
        status=ProjectStatus.ACTIVE,
        active=True,
        count=3,
        note=None,
    )
    assert audit.snapshot_of(obj, ["amount", "on", "at", "status", "active", "count", "note"]) == {
        "amount": "10.50",
        "on": "2026-10-29",
        "at": "2026-10-29T09:30:00+00:00",
        "status": "ACTIVE",
        "active": True,
        "count": 3,
        "note": None,
    }


def _seed_rows(db, actor):
    for entity, eid, action in [
        ("projects", 1, "created"),
        ("projects", 1, "updated"),
        ("users", 7, "updated"),
    ]:
        audit.record(db, entity=entity, entity_id=eid, action=action, actor_id=actor.id)


def test_audit_api_requires_audit_view(client, make_user, auth_header):
    resp = client.get("/api/audit", headers=auth_header(make_user(Role.REQUESTOR)))
    assert resp.status_code == 403


def test_audit_api_filters_by_entity(client, db_session, make_user, auth_header):
    finance = make_user(Role.FINANCE)
    _seed_rows(db_session, finance)

    resp = client.get("/api/audit", params={"entity": "projects"}, headers=auth_header(finance))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {i["entity"] for i in body["items"]} == {"projects"}
    assert [i["action"] for i in body["items"]] == ["updated", "created"]  # newest first


def test_audit_api_date_to_includes_the_whole_day(client, db_session, make_user, auth_header):
    finance = make_user(Role.FINANCE)
    _seed_rows(db_session, finance)
    row_day = db_session.scalar(select(AuditLog.created_at).limit(1)).astimezone(UTC).date()
    headers = auth_header(finance)

    same_day = client.get(
        "/api/audit", params={"date_from": str(row_day), "date_to": str(row_day)}, headers=headers
    )
    assert same_day.json()["total"] == 3
    before = client.get("/api/audit", params={"date_to": str(row_day - timedelta(days=1))}, headers=headers)
    assert before.json()["total"] == 0
    after = client.get("/api/audit", params={"date_from": str(row_day + timedelta(days=1))}, headers=headers)
    assert after.json()["total"] == 0


def test_audit_api_paginates(client, db_session, make_user, auth_header):
    finance = make_user(Role.FINANCE)
    _seed_rows(db_session, finance)

    resp = client.get("/api/audit", params={"size": 1, "page": 2}, headers=auth_header(finance))
    body = resp.json()
    assert (body["total"], body["page"], body["size"], len(body["items"])) == (3, 2, 1, 1)
    assert client.get("/api/audit", params={"size": 101}, headers=auth_header(finance)).status_code == 400
