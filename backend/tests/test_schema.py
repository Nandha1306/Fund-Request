"""Schema-level integrity tests — run against real PostgreSQL.

Each test uses the `db_session` fixture (real PostgreSQL, rolled back).
All tests expect an IntegrityError when the DB rejects a bad insert.
"""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.enums import (
    LedgerEntryType,
    ProjectStatus,
    RequestStatus,
    Role,
)
from app.core.security import hash_password
from app.modules.accounts.models import User
from app.modules.finance.models import BudgetLedger, Liquidation
from app.modules.fund_requests.models import FundRequest
from app.modules.projects.models import Project, ProjectMember

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(db, role=Role.REQUESTOR, suffix="") -> User:
    u = User(
        email=f"schema_{role.value.lower()}{suffix}@test.com",
        full_name="Test",
        password_hash=hash_password("T@1234567"),
        role=role.value,
    )
    db.add(u)
    db.flush()
    return u


def _make_project(db, creator: User, allocated=Decimal("100000.00")) -> Project:
    p = Project(
        code=f"TST-{id(creator)}",
        name="Test Project",
        allocated=allocated,
        status=ProjectStatus.ACTIVE.value,
        created_by=creator.id,
    )
    db.add(p)
    db.flush()
    return p


def _make_fund_request(db, project: Project, requestor: User, approver: User) -> FundRequest:
    fr = FundRequest(
        ref_no=f"FR-TST-{id(requestor)}",
        project_id=project.id,
        requestor_id=requestor.id,
        approver_id=approver.id,
        amount_requested=Decimal("1000.00"),
        purpose="Test purpose",
        status=RequestStatus.PENDING_APPROVAL.value,
    )
    db.add(fr)
    db.flush()
    return fr


# ---------------------------------------------------------------------------
# fund_requests constraints
# ---------------------------------------------------------------------------

class TestFundRequestConstraints:
    def test_self_approval_rejected(self, db_session):
        u = _make_user(db_session, suffix="_sa")
        p = _make_project(db_session, u)
        fr = FundRequest(
            ref_no="FR-SELF-001",
            project_id=p.id,
            requestor_id=u.id,
            approver_id=u.id,  # same user — must fail
            amount_requested=Decimal("500.00"),
            purpose="Self-approval test",
            status=RequestStatus.PENDING_APPROVAL.value,
        )
        db_session.add(fr)
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_amount_requested_zero_rejected(self, db_session):
        req = _make_user(db_session, Role.REQUESTOR, "_z1")
        app = _make_user(db_session, Role.APPROVER, "_z2")
        p = _make_project(db_session, req)
        fr = FundRequest(
            ref_no="FR-ZERO-001",
            project_id=p.id,
            requestor_id=req.id,
            approver_id=app.id,
            amount_requested=Decimal("0.00"),  # must fail
            purpose="Zero amount test",
            status=RequestStatus.PENDING_APPROVAL.value,
        )
        db_session.add(fr)
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_negative_amount_requested_rejected(self, db_session):
        req = _make_user(db_session, Role.REQUESTOR, "_n1")
        app = _make_user(db_session, Role.APPROVER, "_n2")
        p = _make_project(db_session, req)
        fr = FundRequest(
            ref_no="FR-NEG-001",
            project_id=p.id,
            requestor_id=req.id,
            approver_id=app.id,
            amount_requested=Decimal("-100.00"),  # must fail
            purpose="Negative amount test",
            status=RequestStatus.PENDING_APPROVAL.value,
        )
        db_session.add(fr)
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_invalid_status_rejected(self, db_session):
        req = _make_user(db_session, Role.REQUESTOR, "_st1")
        app = _make_user(db_session, Role.APPROVER, "_st2")
        p = _make_project(db_session, req)
        fr = FundRequest(
            ref_no="FR-STAT-001",
            project_id=p.id,
            requestor_id=req.id,
            approver_id=app.id,
            amount_requested=Decimal("500.00"),
            purpose="Status test",
            status="INVALID_STATUS",  # must fail
        )
        db_session.add(fr)
        with pytest.raises(IntegrityError):
            db_session.flush()


# ---------------------------------------------------------------------------
# liquidations: unique per request
# ---------------------------------------------------------------------------

class TestLiquidationConstraints:
    def test_second_liquidation_for_same_request_rejected(self, db_session):
        req = _make_user(db_session, Role.REQUESTOR, "_liq1")
        app = _make_user(db_session, Role.APPROVER, "_liq2")
        fin = _make_user(db_session, Role.FINANCE, "_liq3")
        p = _make_project(db_session, req)
        fr = _make_fund_request(db_session, p, req, app)

        liq1 = Liquidation(
            request_id=fr.id,
            verified_total=Decimal("900.00"),
            disbursed_total=Decimal("1000.00"),
            balance_returned=Decimal("100.00"),
            completed_by=fin.id,
        )
        db_session.add(liq1)
        db_session.flush()

        liq2 = Liquidation(
            request_id=fr.id,  # same request — must fail
            verified_total=Decimal("900.00"),
            disbursed_total=Decimal("1000.00"),
            balance_returned=Decimal("100.00"),
            completed_by=fin.id,
        )
        db_session.add(liq2)
        with pytest.raises(IntegrityError):
            db_session.flush()


# ---------------------------------------------------------------------------
# projects: budget constraint
# ---------------------------------------------------------------------------

class TestProjectBudgetConstraint:
    def test_commitments_exceed_budget_rejected(self, db_session):
        u = _make_user(db_session, suffix="_bud")
        p = Project(
            code="TST-BUD-001",
            name="Over Budget",
            allocated=Decimal("100.00"),
            reserved=Decimal("60.00"),
            outstanding=Decimal("50.00"),  # 60 + 50 > 100 — must fail
            status=ProjectStatus.ACTIVE.value,
            created_by=u.id,
        )
        db_session.add(p)
        with pytest.raises(IntegrityError):
            db_session.flush()

    def test_negative_bucket_rejected(self, db_session):
        u = _make_user(db_session, suffix="_neg_bud")
        p = Project(
            code="TST-NEG-BUD",
            name="Negative Bucket",
            allocated=Decimal("100.00"),
            reserved=Decimal("-10.00"),  # must fail
            status=ProjectStatus.ACTIVE.value,
            created_by=u.id,
        )
        db_session.add(p)
        with pytest.raises(IntegrityError):
            db_session.flush()


# ---------------------------------------------------------------------------
# budget_ledger: non-ALLOCATE rows must have positive amount
# ---------------------------------------------------------------------------

class TestLedgerConstraint:
    def test_reserve_zero_rejected(self, db_session):
        u = _make_user(db_session, suffix="_ledg")
        p = _make_project(db_session, u)
        entry = BudgetLedger(
            project_id=p.id,
            entry_type=LedgerEntryType.RESERVE.value,
            amount=Decimal("0.00"),  # RESERVE must be > 0
            actor_id=u.id,
        )
        db_session.add(entry)
        with pytest.raises(IntegrityError):
            db_session.flush()


# ---------------------------------------------------------------------------
# project_members: unique (project, user)
# ---------------------------------------------------------------------------

class TestProjectMemberConstraint:
    def test_duplicate_member_rejected(self, db_session):
        admin = _make_user(db_session, Role.SUPER_ADMIN, "_mem")
        user = _make_user(db_session, Role.REQUESTOR, "_mem2")
        p = _make_project(db_session, admin)

        db_session.add(ProjectMember(project_id=p.id, user_id=user.id, added_by=admin.id))
        db_session.flush()

        db_session.add(ProjectMember(project_id=p.id, user_id=user.id, added_by=admin.id))  # duplicate
        with pytest.raises(IntegrityError):
            db_session.flush()
