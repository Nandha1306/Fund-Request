"""Budget engine against real PostgreSQL.

After every money movement the project totals must equal what the ledger alone says
(``reconcile``): that is how FR-15, "no double counting", is proven.
"""

from decimal import Decimal as D

import pytest
from sqlalchemy import func, select, text

from app.core.enums import LedgerEntryType, Role
from app.core.errors import BusinessRuleViolation, Conflict
from app.modules.finance import budget
from app.modules.finance.models import BudgetLedger


@pytest.fixture()
def setup(make_user, make_project, make_request):
    requestor = make_user(Role.REQUESTOR)
    approver = make_user(Role.APPROVER)
    project = make_project(allocated="100000.00")
    request = make_request(project, requestor, approver, amount="30000.00")
    return project, request


def _assert_consistent(db, project):
    assert budget.reconcile(db, project.id) == budget.buckets_of(project)


def _ledger_count(db, project) -> int:
    return db.scalar(select(func.count()).where(BudgetLedger.project_id == project.id))


def _entries(db, project) -> list[tuple[str, D, str | None]]:
    return [(r.entry_type, r.amount, r.note) for r in budget.ledger_rows(db, project.id)]


# ---------------------------------------------------------------------------
# Worked example, arch §9.2 (both branches)
# ---------------------------------------------------------------------------


def test_worked_example_overspend_with_top_up(db_session, setup):
    db, (project, request) = db_session, setup
    _assert_consistent(db, project)

    budget.reserve(db, project, request, D("30000"), actor_id=None)
    assert budget.buckets_of(project).available == D("70000.00")
    _assert_consistent(db, project)

    budget.disburse(db, project, request, D("30000"), actor_id=None)
    assert (project.reserved, project.outstanding) == (D("0.00"), D("30000.00"))
    _assert_consistent(db, project)

    budget.adjust_overage(db, project, request, budget.overage_for(D("32000"), D("30000")), actor_id=None)
    assert budget.buckets_of(project).available == D("68000.00")
    _assert_consistent(db, project)

    s = budget.settle(db, project, request, verified=D("32000"), disbursed=D("30000"), actor_id=None)
    assert s.top_up == D("2000.00")
    assert budget.buckets_of(project) == budget.Buckets(D("100000.00"), D("0.00"), D("0.00"), D("32000.00"))
    assert budget.buckets_of(project).available == D("68000.00")
    assert request.overage_reserved == D("0.00")
    _assert_consistent(db, project)

    assert [e[0] for e in _entries(db, project)] == [
        "ALLOCATE",
        "RESERVE",
        "DISBURSE",
        "RESERVE",
        "LIQUIDATE",
        "TOPUP",
    ]


def test_worked_example_underspend_with_balance_return(db_session, setup):
    db, (project, request) = db_session, setup
    budget.reserve(db, project, request, D("30000"), actor_id=None)
    budget.disburse(db, project, request, D("30000"), actor_id=None)

    s = budget.settle(db, project, request, verified=D("27500"), disbursed=D("30000"), actor_id=None)
    assert (s.balance_return, s.top_up) == (D("2500.00"), D("0.00"))
    assert (project.outstanding, project.liquidated) == (D("0.00"), D("27500.00"))
    assert budget.buckets_of(project).available == D("72500.00")
    assert ("RETURN", D("2500.00"), None) in _entries(db, project)
    _assert_consistent(db, project)


# ---------------------------------------------------------------------------
# No-ops and guards
# ---------------------------------------------------------------------------


def test_zero_reserve_and_empty_release_write_no_ledger_row(db_session, setup):
    db, (project, request) = db_session, setup
    before = _ledger_count(db, project)
    assert budget.reserve(db, project, request, D("0"), actor_id=None) == D("0.00")
    assert budget.release(db, project, request, actor_id=None) == D("0.00")
    assert _ledger_count(db, project) == before


def test_negative_or_zero_amounts_are_programming_errors(db_session, setup):
    db, (project, request) = db_session, setup
    with pytest.raises(ValueError):
        budget.reserve(db, project, request, D("-1"), actor_id=None)
    with pytest.raises(ValueError):
        budget.disburse(db, project, request, D("0"), actor_id=None)
    with pytest.raises(ValueError):
        budget.adjust_overage(db, project, request, D("-5"), actor_id=None)


def test_reserve_above_available_is_422_and_changes_nothing(db_session, setup):
    db, (project, request) = db_session, setup
    before, count = budget.buckets_of(project), _ledger_count(db, project)

    with pytest.raises(BusinessRuleViolation) as exc:
        budget.reserve(db, project, request, D("100000.01"), actor_id=None)

    assert exc.value.details == {"available": "100000.00"}
    assert budget.buckets_of(project) == before
    assert request.reserved_amount == D("0.00")
    assert _ledger_count(db, project) == count


def test_release_returns_the_whole_reservation(db_session, setup):
    db, (project, request) = db_session, setup
    budget.reserve(db, project, request, D("12000"), actor_id=None)
    assert budget.release(db, project, request, actor_id=None) == D("12000.00")
    assert (project.reserved, request.reserved_amount) == (D("0.00"), D("0.00"))
    _assert_consistent(db, project)


def test_disburse_more_than_reserved_is_409(db_session, setup):
    db, (project, request) = db_session, setup
    budget.reserve(db, project, request, D("1000"), actor_id=None)
    with pytest.raises(Conflict):
        budget.disburse(db, project, request, D("1000.01"), actor_id=None)


def test_adjust_overage_up_past_available_is_422(db_session, setup):
    db, (project, request) = db_session, setup
    budget.reserve(db, project, request, D("100000"), actor_id=None)  # available is now 0
    budget.disburse(db, project, request, D("100000"), actor_id=None)
    with pytest.raises(BusinessRuleViolation):
        budget.adjust_overage(db, project, request, D("0.01"), actor_id=None)


def test_adjust_overage_down_releases_the_difference(db_session, setup):
    db, (project, request) = db_session, setup
    budget.adjust_overage(db, project, request, D("2000"), actor_id=None)
    budget.adjust_overage(db, project, request, D("500"), actor_id=None)
    budget.adjust_overage(db, project, request, D("500"), actor_id=None)  # unchanged: no row

    assert (project.reserved, request.overage_reserved) == (D("500.00"), D("500.00"))
    overage_rows = [e for e in _entries(db, project) if e[2] == "overage"]
    assert overage_rows == [("RESERVE", D("2000.00"), "overage"), ("RELEASE", D("1500.00"), "overage")]
    _assert_consistent(db, project)


def test_settle_refuses_while_money_is_still_reserved(db_session, setup):
    db, (project, request) = db_session, setup
    budget.reserve(db, project, request, D("30000"), actor_id=None)
    budget.disburse(db, project, request, D("20000"), actor_id=None)  # 10,000 still reserved
    with pytest.raises(Conflict):
        budget.settle(db, project, request, verified=D("20000"), disbursed=D("20000"), actor_id=None)


def test_settle_refuses_a_top_up_that_was_never_reserved(db_session, setup):
    db, (project, request) = db_session, setup
    budget.reserve(db, project, request, D("30000"), actor_id=None)
    budget.disburse(db, project, request, D("30000"), actor_id=None)
    with pytest.raises(Conflict):  # 2,000 over, but no overage reservation exists
        budget.settle(db, project, request, verified=D("32000"), disbursed=D("30000"), actor_id=None)


def test_change_allocation_below_commitments_is_422(db_session, setup):
    db, (project, request) = db_session, setup
    budget.reserve(db, project, request, D("60000"), actor_id=None)
    with pytest.raises(BusinessRuleViolation) as exc:
        budget.change_allocation(db, project, D("59999.99"), actor_id=None)
    assert exc.value.details == {"committed": "60000.00"}


def test_change_allocation_writes_the_signed_change(db_session, setup):
    db, (project, _) = db_session, setup
    budget.change_allocation(db, project, D("90000"), actor_id=None)
    budget.change_allocation(db, project, D("90000"), actor_id=None)  # no change: no row
    budget.change_allocation(db, project, D("120000"), actor_id=None)

    allocations = [e[1] for e in _entries(db, project) if e[0] == LedgerEntryType.ALLOCATE.value]
    assert allocations == [D("100000.00"), D("-10000.00"), D("30000.00")]
    assert project.allocated == D("120000.00")
    _assert_consistent(db, project)


def test_lock_project_refreshes_stale_in_memory_numbers(db_session, setup):
    """Without populate_existing the session would hand back the stale cached object."""
    db, (project, _) = db_session, setup
    assert project.reserved == D("0.00")

    db.execute(text("UPDATE projects SET reserved = reserved + 5 WHERE id = :id"), {"id": project.id})
    assert project.reserved == D("0.00")  # the in-memory object does not know yet

    locked = budget.lock_project(db, project.id)
    assert locked is project
    assert project.reserved == D("5.00")
