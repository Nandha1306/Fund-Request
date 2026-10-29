"""The budget engine.

**This is the only code allowed to change** ``projects.allocated / reserved /
outstanding / liquidated`` and ``fund_requests.reserved_amount / overage_reserved``.

Structure (phase 2 design choices):
- *Pure functions* hold the arithmetic.  No database, unit-testable, every result
  passes through ``q2()``.
- *Database functions* lock the project, apply the movement and append **one ledger row
  per movement**.  They run inside the caller's transaction and never commit — the
  service layer owns the commit.
- ``reconcile()`` rebuilds the four buckets from ``budget_ledger`` alone, which is how
  the tests prove there is no double counting (FR-15).

Lock order is always project, then request.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.enums import LedgerEntryType
from app.core.errors import BusinessRuleViolation, Conflict, NotFound
from app.core.money import ZERO, q2
from app.modules.finance.models import BudgetLedger
from app.modules.fund_requests.models import FundRequest
from app.modules.projects.models import Project

# ---------------------------------------------------------------------------
# Pure functions (no database)
# ---------------------------------------------------------------------------

def available(allocated: Decimal, reserved: Decimal, outstanding: Decimal, liquidated: Decimal) -> Decimal:
    """Money still free to commit: allocation minus everything already spoken for."""
    return q2(allocated - reserved - outstanding - liquidated)


def committed(reserved: Decimal, outstanding: Decimal, liquidated: Decimal) -> Decimal:
    """Total commitments the allocation has to cover (arch §11.3 #14)."""
    return q2(reserved + outstanding + liquidated)


def reservation_for_submit(amount: Decimal, already_disbursed: Decimal) -> Decimal:
    """Reservation raised on submit / resubmit.

    D3: money that has already been paid out is outstanding, not reserved, so a
    resubmission after a payout reserves only the shortfall — 0 when fully disbursed.
    """
    return q2(max(q2(amount) - q2(already_disbursed), ZERO))


def overage_for(expense_total: Decimal, disbursed: Decimal) -> Decimal:
    """Extra reservation needed when the expenses exceed the money disbursed."""
    return q2(max(q2(expense_total) - q2(disbursed), ZERO))


@dataclass(frozen=True)
class Settlement:
    liquidated: Decimal
    balance_return: Decimal
    top_up: Decimal


def settlement(verified: Decimal, disbursed: Decimal) -> Settlement:
    """Split a verified total against the money disbursed (arch §9.2 both branches)."""
    verified, disbursed = q2(verified), q2(disbursed)
    return Settlement(
        liquidated=verified,
        balance_return=q2(max(disbursed - verified, ZERO)),
        top_up=q2(max(verified - disbursed, ZERO)),
    )


def overage_delta(total_expenses: Decimal, disbursed: Decimal, current_overage: Decimal) -> Decimal:
    """Signed change needed on the overage reservation when expenses are (re)submitted."""
    return q2(overage_for(total_expenses, disbursed) - q2(current_overage))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ledger(
    db: Session,
    project: Project,
    request: FundRequest | None,
    entry_type: LedgerEntryType,
    amount: Decimal,
    actor_id: int | None,
    note: str | None = None,
) -> None:
    db.add(
        BudgetLedger(
            project_id=project.id,
            request_id=request.id if request is not None else None,
            entry_type=entry_type.value,
            amount=amount,
            actor_id=actor_id,
            note=note,
        )
    )


def _available(project: Project) -> Decimal:
    return available(project.allocated, project.reserved, project.outstanding, project.liquidated)


# ---------------------------------------------------------------------------
# Database functions (caller's transaction, never commit)
# ---------------------------------------------------------------------------

def lock_project(db: Session, project_id: int) -> Project:
    """``SELECT … FOR UPDATE`` and **refresh** the in-memory object.

    ``populate_existing`` matters: without it SQLAlchemy hands back the object the
    session already loaded, so ``available`` would be computed from stale numbers and a
    concurrent commit would blow the CHECK constraint (a 500) instead of a clean 422.
    """
    project = db.execute(
        select(Project).where(Project.id == project_id).with_for_update().execution_options(
            populate_existing=True
        )
    ).scalar_one_or_none()
    if project is None:
        raise NotFound("Project not found")
    return project


def reserve(
    db: Session, project: Project, request: FundRequest, amount: Decimal, *, actor_id: int | None
) -> Decimal:
    """Reserve *amount* against the project budget and the request."""
    amount = q2(amount)
    if amount < ZERO:
        raise ValueError("reserve() requires a non-negative amount")
    if amount == ZERO:
        return ZERO  # nothing to reserve (D3 resubmission of a fully disbursed amount)

    free = _available(project)
    if amount > free:
        raise BusinessRuleViolation(
            "Amount exceeds the project's available budget",
            details={"available": str(free)},
        )

    project.reserved = q2(project.reserved + amount)
    request.reserved_amount = q2(request.reserved_amount + amount)
    _ledger(db, project, request, LedgerEntryType.RESERVE, amount, actor_id)
    db.flush()
    return amount


def release(db: Session, project: Project, request: FundRequest, *, actor_id: int | None) -> Decimal:
    """Release whatever the request still holds; a no-op when nothing is reserved."""
    amount = q2(request.reserved_amount)
    if amount == ZERO:
        return ZERO

    project.reserved = q2(project.reserved - amount)
    request.reserved_amount = ZERO
    _ledger(db, project, request, LedgerEntryType.RELEASE, amount, actor_id)
    db.flush()
    return amount


def disburse(
    db: Session, project: Project, request: FundRequest, amount: Decimal, *, actor_id: int | None
) -> Decimal:
    """Move reserved money to outstanding.  Never more than the request holds."""
    amount = q2(amount)
    if amount <= ZERO:
        raise ValueError("disburse() requires a positive amount")
    reserved = q2(request.reserved_amount)
    if amount > reserved:
        raise Conflict(
            "Disbursement exceeds the reserved amount",
            details={"reserved": str(reserved)},
        )

    project.reserved = q2(project.reserved - amount)
    project.outstanding = q2(project.outstanding + amount)
    request.reserved_amount = q2(reserved - amount)
    _ledger(db, project, request, LedgerEntryType.DISBURSE, amount, actor_id)
    db.flush()
    return amount


def adjust_overage(
    db: Session,
    project: Project,
    request: FundRequest,
    new_overage: Decimal,
    *,
    actor_id: int | None,
) -> Decimal:
    """Set the request's overage reservation to *new_overage*, reserving/releasing the delta."""
    new_overage = q2(new_overage)
    if new_overage < ZERO:
        raise ValueError("adjust_overage() requires a non-negative overage")

    delta = q2(new_overage - q2(request.overage_reserved))
    if delta == ZERO:
        return new_overage

    if delta > ZERO:
        free = _available(project)
        if delta > free:
            raise BusinessRuleViolation(
                "Amount exceeds the project's available budget",
                details={"available": str(free), "required": str(delta)},
            )
        project.reserved = q2(project.reserved + delta)
        _ledger(db, project, request, LedgerEntryType.RESERVE, delta, actor_id, note="overage")
    else:
        project.reserved = q2(project.reserved + delta)
        _ledger(db, project, request, LedgerEntryType.RELEASE, q2(-delta), actor_id, note="overage")

    request.overage_reserved = new_overage
    db.flush()
    return new_overage


def settle(
    db: Session,
    project: Project,
    request: FundRequest,
    *,
    verified: Decimal,
    disbursed: Decimal,
    actor_id: int | None,
) -> Settlement:
    """Liquidate a request: outstanding and overage clear, the verified total becomes liquidated."""
    verified, disbursed = q2(verified), q2(disbursed)
    if verified <= ZERO:
        raise ValueError("settle() requires a positive verified total")
    if q2(request.reserved_amount) != ZERO:
        raise Conflict(
            "Request still holds an unpaid reservation",
            details={"reserved": str(q2(request.reserved_amount))},
        )

    result = settlement(verified, disbursed)
    overage = q2(request.overage_reserved)
    if result.top_up != overage:
        # Defensive invariant: every cent of top-up must already be reserved as overage.
        raise Conflict(
            "Top-up does not match the reserved overage",
            details={"top_up": str(result.top_up), "overage_reserved": str(overage)},
        )

    project.outstanding = q2(project.outstanding - disbursed)
    project.reserved = q2(project.reserved - overage)
    project.liquidated = q2(project.liquidated + verified)
    request.overage_reserved = ZERO

    _ledger(db, project, request, LedgerEntryType.LIQUIDATE, verified, actor_id)
    if result.balance_return > ZERO:
        _ledger(db, project, request, LedgerEntryType.RETURN, result.balance_return, actor_id)
    if result.top_up > ZERO:
        _ledger(db, project, request, LedgerEntryType.TOPUP, result.top_up, actor_id)
    db.flush()
    return result


def change_allocation(
    db: Session, project: Project, new_allocation: Decimal, *, actor_id: int | None
) -> Decimal:
    """Set the project allocation and write the signed change to the ledger."""
    new_allocation = q2(new_allocation)
    if new_allocation < ZERO:
        raise ValueError("change_allocation() requires a non-negative allocation")

    delta = q2(new_allocation - q2(project.allocated))
    if delta == ZERO:
        return new_allocation

    committed_total = committed(project.reserved, project.outstanding, project.liquidated)
    if new_allocation < committed_total:
        raise BusinessRuleViolation(
            "New allocation is below existing commitments",
            details={"committed": str(committed_total)},
        )

    project.allocated = new_allocation
    _ledger(db, project, None, LedgerEntryType.ALLOCATE, delta, actor_id, note="Allocation change")
    db.flush()
    return new_allocation


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Buckets:
    allocated: Decimal
    reserved: Decimal
    outstanding: Decimal
    liquidated: Decimal

    @property
    def available(self) -> Decimal:
        return available(self.allocated, self.reserved, self.outstanding, self.liquidated)


def buckets_of(project: Project) -> Buckets:
    """The totals currently stored on the project row, for comparison with ``reconcile``."""
    return Buckets(
        allocated=q2(project.allocated),
        reserved=q2(project.reserved),
        outstanding=q2(project.outstanding),
        liquidated=q2(project.liquidated),
    )


_RECONCILE_SIGNS: dict[LedgerEntryType, tuple[int, int, int]] = {
    #                   reserved  outstanding  liquidated
    LedgerEntryType.RESERVE: (+1, 0, 0),
    LedgerEntryType.RELEASE: (-1, 0, 0),
    LedgerEntryType.DISBURSE: (-1, +1, 0),
    LedgerEntryType.TOPUP: (-1, +1, 0),
    LedgerEntryType.LIQUIDATE: (0, -1, +1),
    LedgerEntryType.RETURN: (0, -1, 0),
}


def reconcile(db: Session, project_id: int) -> Buckets:
    """Rebuild the four buckets from ``budget_ledger`` alone.

    The ledger is the source of truth; the columns on ``projects`` are a cache.  The
    tests assert ``reconcile() == buckets_of(project)`` after every scenario.
    """
    stmt: Select = (
        select(BudgetLedger.entry_type, func.coalesce(func.sum(BudgetLedger.amount), ZERO))
        .where(BudgetLedger.project_id == project_id)
        .group_by(BudgetLedger.entry_type)
    )
    sums: dict[str, Decimal] = {entry.value: ZERO for entry in LedgerEntryType}
    for entry_type, total in db.execute(stmt).all():
        sums[entry_type] = q2(total)

    allocated = sums[LedgerEntryType.ALLOCATE.value]
    reserved = outstanding = liquidated = ZERO
    for entry, (r_sign, o_sign, l_sign) in _RECONCILE_SIGNS.items():
        amount = sums[entry.value]
        reserved += r_sign * amount
        outstanding += o_sign * amount
        liquidated += l_sign * amount

    return Buckets(q2(allocated), q2(reserved), q2(outstanding), q2(liquidated))


def ledger_rows(db: Session, project_id: int) -> Iterable[BudgetLedger]:
    """All ledger rows of a project, oldest first (used by tests and the ledger view)."""
    return db.scalars(
        select(BudgetLedger).where(BudgetLedger.project_id == project_id).order_by(BudgetLedger.id)
    ).all()
