"""Races against real PostgreSQL with real commits (arch §11.2).

These tests cannot use the rolled-back ``db_session``: each HTTP call gets its own session from a
private sessionmaker on the test database, so two threads really hold competing transactions.  The
``committed`` fixture removes everything it wrote with ``TRUNCATE … RESTART IDENTITY CASCADE``.
"""

import threading
from collections.abc import Callable
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app.core.db import get_db
from app.core.enums import LedgerEntryType, ProjectStatus, Role
from app.core.security import create_access_token, hash_password
from app.modules.accounts.models import User
from app.modules.finance import budget
from app.modules.finance.models import BudgetLedger, Disbursement
from app.modules.projects.models import Project, ProjectMember

_TABLES = (
    "notifications, audit_log, disbursements, budget_ledger, fund_requests, project_members, projects, users"
)


@pytest.fixture()
def committed(app, test_engine):
    """Committed users + a 20,000 project, an app whose every request opens its own session."""
    Session = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def _get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    tag = uuid4().hex[:8]
    with Session() as db:
        def user(role: Role, name: str) -> User:
            u = User(email=f"race_{name}_{tag}@example.com", full_name=name, password_hash=hash_password("x"),
                     role=role.value)
            db.add(u)
            return u

        people = SimpleNamespace(
            requestor=user(Role.REQUESTOR, "requestor"),
            approver=user(Role.APPROVER, "approver"),
            approver2=user(Role.APPROVER, "approver2"),
            finance=user(Role.FINANCE, "finance"),
        )
        db.flush()
        project = Project(code=f"RACE-{tag}".upper(), name="Race", allocated=D("20000.00"),
                          status=ProjectStatus.ACTIVE.value, created_by=people.finance.id)
        db.add(project)
        db.flush()
        db.add(BudgetLedger(project_id=project.id, entry_type=LedgerEntryType.ALLOCATE.value,
                            amount=D("20000.00"), actor_id=people.finance.id))
        db.add(ProjectMember(project_id=project.id, user_id=people.requestor.id, added_by=people.finance.id))
        db.commit()

    def headers(u: User, **extra) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(u.id)}", **extra}

    yield SimpleNamespace(app=app, Session=Session, people=people, project_id=project.id, headers=headers)

    app.dependency_overrides.clear()
    with test_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))


def _race(app, *calls: Callable[[TestClient], object]) -> list:
    """Run every call in its own thread, released together by a barrier."""
    barrier = threading.Barrier(len(calls))
    results: list = [None] * len(calls)

    def run(i: int, call) -> None:
        client = TestClient(app, raise_server_exceptions=False)
        barrier.wait()
        results[i] = call(client)

    threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def _create(w, client: TestClient, amount: str):
    return client.post(
        "/api/requests",
        json={"project_id": w.project_id, "approver_id": w.people.approver.id, "amount_requested": amount,
              "purpose": "race"},
        headers=w.headers(w.people.requestor),
    )


def _approved_request(w) -> int:
    client = TestClient(w.app)
    rid = _create(w, client, "5000").json()["id"]
    headers = w.headers(w.people.approver)
    resp = client.post(f"/api/requests/{rid}/approve", json={"version": 1}, headers=headers)
    assert resp.status_code == 200, resp.text
    return rid


def _disburse(w, rid: int, key: str):
    def call(client: TestClient):
        return client.post(
            f"/api/requests/{rid}/disburse",
            json={"version": 2, "txn_ref": f"NEFT-{uuid4().hex[:8]}", "disbursed_on": str(date.today())},
            headers=w.headers(w.people.finance, **{"Idempotency-Key": key}),
        )

    return call


def _assert_reconciled(w) -> Project:
    with w.Session() as db:
        project = db.get(Project, w.project_id)
        assert budget.reconcile(db, w.project_id) == budget.buckets_of(project)
        return project


def test_two_requests_cannot_overspend_the_budget(committed):
    w = committed
    results = _race(w.app, lambda c: _create(w, c, "15000"), lambda c: _create(w, c, "10000"))
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 422], [r.text for r in results]

    winner = next(r for r in results if r.status_code == 201).json()
    project = _assert_reconciled(w)
    # Whichever arrived first holds the money; the other did not fit in what was left.
    assert project.reserved == D(winner["amount_requested"])
    assert project.reserved in (D("15000.00"), D("10000.00"))


def test_two_disbursements_with_different_keys_pay_once(committed):
    w = committed
    rid = _approved_request(w)
    first, second = _disburse(w, rid, f"key-a-{uuid4().hex}"), _disburse(w, rid, f"key-b-{uuid4().hex}")
    results = _race(w.app, first, second)
    assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
    with w.Session() as db:
        assert db.scalar(select(func.count(Disbursement.id)).where(Disbursement.request_id == rid)) == 1
    project = _assert_reconciled(w)
    assert (project.reserved, project.outstanding) == (D("0.00"), D("5000.00"))


def test_same_key_twice_writes_one_row(committed):
    w = committed
    rid = _approved_request(w)
    key = f"key-same-{uuid4().hex}"
    results = _race(w.app, _disburse(w, rid, key), _disburse(w, rid, key))
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    assert {r.json()["version"] for r in results} == {3}
    with w.Session() as db:
        assert db.scalar(select(func.count(Disbursement.id)).where(Disbursement.request_id == rid)) == 1
    _assert_reconciled(w)


def test_approve_and_reassign_exactly_one_wins(committed):
    w = committed
    rid = _create(w, TestClient(w.app), "5000").json()["id"]

    def approve(client: TestClient):
        return client.post(f"/api/requests/{rid}/approve", json={"version": 1},
                           headers=w.headers(w.people.approver))

    def reassign(client: TestClient):
        return client.post(f"/api/requests/{rid}/reassign",
                           json={"version": 1, "approver_id": w.people.approver2.id},
                           headers=w.headers(w.people.finance))

    results = _race(w.app, approve, reassign)
    codes = [r.status_code for r in results]
    assert codes.count(200) == 1, [r.text for r in results]
    # The loser saw a stale version (409), or — if reassign committed before the approver even looked —
    # the request is simply no longer the approver's (404).
    assert all(c in (200, 404, 409) for c in codes)
    with w.Session() as db:
        version = db.scalar(text("SELECT version FROM fund_requests WHERE id = :id"), {"id": rid})
    assert version == 2
    _assert_reconciled(w)
