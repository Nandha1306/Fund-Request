"""Pytest fixtures for Phase 1+.

Key design decisions (from corrected plan):
- Uses fundreq_test as the test database (never touches the dev DB).
- Admin connection uses make_url().set(database="postgres") to avoid replacing
  the username when the URL contains "fundreq" in multiple places.
- Alembic is invoked as [sys.executable, "-m", "alembic", ...] so it works
  regardless of whether the venv is activated in the shell.
- Each test runs in a transaction that is rolled back; service-level commits
  become savepoints via join_transaction_mode="create_savepoint".
- The `client` fixture overrides get_db with the test session so API calls
  hit the test database, not the dev database.
- The rate limiter is disabled in the `app` fixture.
"""

import os
import subprocess
import sys
from collections.abc import Generator
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.enums import LedgerEntryType, ProjectStatus, RequestStatus, Role
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.modules.accounts.models import User
from app.modules.finance.models import BudgetLedger
from app.modules.fund_requests.models import FundRequest
from app.modules.projects.models import Project, ProjectMember

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Tests create many users; full-cost bcrypt (12 rounds) would dominate the run time.
_real_gensalt = bcrypt.gensalt
bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _real_gensalt(rounds=4, prefix=prefix)

_dev_url = make_url(get_settings().database_url)
_test_url = (
    make_url(os.environ["TEST_DATABASE_URL"])
    if "TEST_DATABASE_URL" in os.environ
    else _dev_url.set(database="fundreq_test")
)


# ---------------------------------------------------------------------------
# Session-scoped: database creation + migration (runs once per pytest session)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_engine():
    """Create fundreq_test if it doesn't exist, migrate it, return the engine."""
    # Connect to the *postgres* maintenance DB — safe even when the URL has
    # "fundreq" as both the username and the database name.
    admin_url = _dev_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"),
            {"n": _test_url.database},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{_test_url.database}"'))
    admin_engine.dispose()

    # Run the real migration so the migration file itself is tested.
    env = {
        **os.environ,
        "DATABASE_URL": _test_url.render_as_string(hide_password=False),
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
    )

    engine = create_engine(_test_url)
    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Function-scoped: each test gets a rolled-back transaction
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session(test_engine) -> Generator[Session, None, None]:
    """Wrap each test in a transaction; commits inside services become savepoints."""
    conn = test_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    yield session
    session.close()
    outer.rollback()
    conn.close()


# ---------------------------------------------------------------------------
# App + client fixtures (test DB wired in)
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    from app.core.rate_limit import limiter
    limiter.enabled = False
    application = create_app()
    yield application
    limiter.enabled = get_settings().rate_limit_enabled  # restore


@pytest.fixture()
def client(app, db_session) -> Generator[TestClient, None, None]:
    """TestClient wired to the test session so API calls never touch the dev DB."""
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Helper fixture: quick user factory
# ---------------------------------------------------------------------------

@pytest.fixture()
def make_user(db_session: Session):
    """Factory: create a User in the test session.

    Usage::
        user = make_user(Role.FINANCE)
        user = make_user(Role.SUPER_ADMIN, can_finance_ops=True, email="x@y.com")
    """
    created: list[User] = []

    def _factory(
        role: Role,
        *,
        email: str | None = None,
        password: str = "Test@1234",
        full_name: str = "Test User",
        is_active: bool = True,
        can_finance_ops: bool = False,
    ) -> User:
        count = len(created)
        user = User(
            email=email or f"test_{role.value.lower()}_{count}@example.com",
            full_name=full_name,
            password_hash=hash_password(password),
            role=role.value,
            is_active=is_active,
            can_finance_ops=can_finance_ops,
        )
        db_session.add(user)
        db_session.flush()
        created.append(user)
        return user

    return _factory


@pytest.fixture()
def auth_header():
    """auth_header(user) -> {"Authorization": "Bearer …"}; skips the (bcrypt) login round trip."""

    def _header(user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(user.id)}"}

    return _header


@pytest.fixture()
def make_project(db_session: Session, make_user):
    """Factory: a project with ``allocated`` set **and** its ALLOCATE ledger row, so
    ``budget.reconcile()`` matches the project row from the start."""

    def _factory(
        *,
        allocated: Decimal | str = "100000.00",
        status: ProjectStatus = ProjectStatus.ACTIVE,
        code: str | None = None,
        members: tuple[User, ...] = (),
        created_by: User | None = None,
    ) -> Project:
        creator = created_by or make_user(Role.FINANCE)
        allocated = Decimal(allocated)
        project = Project(
            code=code or f"T-{uuid4().hex[:8].upper()}",
            name="Test project",
            allocated=allocated,
            status=status.value,
            created_by=creator.id,
        )
        db_session.add(project)
        db_session.flush()
        if allocated:
            db_session.add(
                BudgetLedger(
                    project_id=project.id,
                    entry_type=LedgerEntryType.ALLOCATE.value,
                    amount=allocated,
                    actor_id=creator.id,
                )
            )
        for member in members:
            db_session.add(ProjectMember(project_id=project.id, user_id=member.id, added_by=creator.id))
        db_session.flush()
        return project

    return _factory


@pytest.fixture()
def make_request(db_session: Session):
    """Factory: insert a FundRequest row directly (the request services arrive in Phase 3)."""

    def _factory(
        project: Project,
        requestor: User,
        approver: User,
        *,
        status: RequestStatus = RequestStatus.PENDING_APPROVAL,
        amount: Decimal | str = "1000.00",
    ) -> FundRequest:
        request = FundRequest(
            ref_no=f"FR-T-{uuid4().hex[:8].upper()}",
            project_id=project.id,
            requestor_id=requestor.id,
            approver_id=approver.id,
            amount_requested=Decimal(amount),
            purpose="test",
            status=status.value,
        )
        db_session.add(request)
        db_session.flush()
        return request

    return _factory


# ---------------------------------------------------------------------------
# Phase 3: people and a small client for the request workflow
# ---------------------------------------------------------------------------

@pytest.fixture()
def people(make_user):
    """One of each actor the request workflow needs (all active)."""
    return SimpleNamespace(
        requestor=make_user(Role.REQUESTOR, full_name="Requestor A"),
        requestor2=make_user(Role.REQUESTOR, full_name="Requestor B"),
        approver=make_user(Role.APPROVER, full_name="Approver A"),
        approver2=make_user(Role.APPROVER, full_name="Approver B"),
        finance=make_user(Role.FINANCE, full_name="Finance Officer"),
    )


class Flow:
    """Thin wrapper over the request endpoints so tests read like the workflow."""

    def __init__(self, client: TestClient, auth_header, people, project: Project) -> None:
        self.client, self.auth, self.people, self.project = client, auth_header, people, project

    def create(self, user=None, *, project=None, approver=None, amount="30000.00", **extra):
        body = {
            "project_id": (project or self.project).id,
            "approver_id": (approver or self.people.approver).id,
            "amount_requested": str(amount),
            "purpose": "Field visit",
            **extra,
        }
        return self.client.post("/api/requests", json=body, headers=self.auth(user or self.people.requestor))

    def raised(self, **kwargs) -> dict:
        resp = self.create(**kwargs)
        assert resp.status_code == 201, resp.text
        return resp.json()

    def get(self, user, request_id: int):
        return self.client.get(f"/api/requests/{request_id}", headers=self.auth(user))

    def act(self, user, request_id: int, action: str, *, headers=None, **body):
        if "version" not in body:
            body["version"] = self.get(self.people.finance, request_id).json()["version"]
        return self.client.post(
            f"/api/requests/{request_id}/{action}", json=body, headers={**self.auth(user), **(headers or {})}
        )

    def resubmit(self, user, request_id: int, *, amount="30000.00", approver=None, **extra):
        body = {
            "version": self.get(self.people.finance, request_id).json()["version"],
            "approver_id": (approver or self.people.approver).id,
            "amount_requested": str(amount),
            "purpose": "Field visit (corrected)",
            **extra,
        }
        return self.client.put(f"/api/requests/{request_id}", json=body, headers=self.auth(user))

    def disburse(self, request_id: int, *, user=None, key=None, txn_ref=None, **extra):
        key = key or f"test-key-{uuid4().hex}"
        txn_ref = f"NEFT-{uuid4().hex[:10]}" if txn_ref is None else txn_ref
        body = {"txn_ref": txn_ref, "disbursed_on": str(date.today()), **extra}
        headers = {"Idempotency-Key": key}
        return self.act(user or self.people.finance, request_id, "disburse", headers=headers, **body)

    def ok(self, resp) -> dict:
        assert resp.status_code == 200, resp.text
        return resp.json()


@pytest.fixture()
def flow(client, auth_header, people, make_project) -> Flow:
    project = make_project(allocated="100000.00", members=(people.requestor,), created_by=people.finance)
    return Flow(client, auth_header, people, project)
