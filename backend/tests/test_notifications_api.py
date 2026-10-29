"""Notifications API: own rows only, unread count, mark one / all read."""

from sqlalchemy import select

from app.modules.audit.models import Notification


def _get(flow, user, path="/api/notifications", **params):
    resp = flow.client.get(path, params=params, headers=flow.auth(user))
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_list_is_own_newest_first(flow, people):
    first, second = flow.raised(amount="10"), flow.raised(amount="20")
    body = _get(flow, people.approver)
    assert body["total"] == 2
    assert [n["request_id"] for n in body["items"]] == [second["id"], first["id"]]
    item = body["items"][0]
    assert set(item) == {"id", "type", "message", "request_id", "read_at", "created_at"}
    assert item["type"] == "request_submitted" and item["read_at"] is None
    assert _get(flow, people.requestor)["total"] == 0
    assert _get(flow, people.approver2)["total"] == 0


def test_unread_count_and_mark_read(db_session, flow, people):
    flow.raised(amount="10")
    flow.raised(amount="20")
    assert _get(flow, people.approver, "/api/notifications/unread-count") == {"count": 2}

    newest = _get(flow, people.approver)["items"][0]
    resp = flow.client.post(f"/api/notifications/{newest['id']}/read", headers=flow.auth(people.approver))
    assert resp.status_code == 204
    read_at = db_session.scalar(select(Notification.read_at).where(Notification.id == newest["id"]))
    assert read_at is not None
    again = flow.client.post(f"/api/notifications/{newest['id']}/read", headers=flow.auth(people.approver))
    assert again.status_code == 204  # idempotent

    assert _get(flow, people.approver, "/api/notifications/unread-count") == {"count": 1}
    unread = _get(flow, people.approver, unread_only=True)
    assert unread["total"] == 1 and unread["items"][0]["id"] != newest["id"]

    resp = flow.client.post("/api/notifications/read-all", headers=flow.auth(people.approver))
    assert resp.status_code == 204
    assert _get(flow, people.approver, "/api/notifications/unread-count") == {"count": 0}
    assert _get(flow, people.approver)["total"] == 2  # still listed, just read


def test_someone_elses_notification_is_404(flow, people):
    flow.raised()
    note = _get(flow, people.approver)["items"][0]
    resp = flow.client.post(f"/api/notifications/{note['id']}/read", headers=flow.auth(people.requestor))
    assert resp.status_code == 404
    resp = flow.client.post("/api/notifications/999999/read", headers=flow.auth(people.approver))
    assert resp.status_code == 404
    assert _get(flow, people.approver, "/api/notifications/unread-count") == {"count": 1}


def test_read_all_only_touches_own_rows(flow, people):
    flow.raised()  # notifies the approver
    flow.client.post("/api/notifications/read-all", headers=flow.auth(people.requestor))
    assert _get(flow, people.approver, "/api/notifications/unread-count") == {"count": 1}


def test_pagination(flow, people):
    for _ in range(3):
        flow.raised(amount="10")
    body = _get(flow, people.approver, page=2, size=2)
    assert (body["total"], body["page"], body["size"], len(body["items"])) == (3, 2, 2, 1)


def test_requires_authentication(client):
    assert client.get("/api/notifications").status_code == 401
    assert client.get("/api/notifications/unread-count").status_code == 401
