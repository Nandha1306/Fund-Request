"""Budget arithmetic only: no database (arch §9)."""

from decimal import Decimal as D

from app.core.money import q2
from app.modules.finance import budget


def test_available_and_committed():
    assert budget.available(D("100000"), D("30000"), D("20000"), D("10000")) == D("40000.00")
    assert budget.committed(D("30000"), D("20000"), D("10000")) == D("60000.00")


def test_reservation_for_submit():
    assert budget.reservation_for_submit(D("30000"), D("0")) == D("30000.00")
    assert budget.reservation_for_submit(D("30000"), D("12000")) == D("18000.00")
    # D3: resubmitting an amount that was already paid out reserves nothing more
    assert budget.reservation_for_submit(D("5000"), D("5000")) == D("0.00")
    assert budget.reservation_for_submit(D("4000"), D("5000")) == D("0.00")


def test_overage_for():
    assert budget.overage_for(D("32000"), D("30000")) == D("2000.00")
    assert budget.overage_for(D("27500"), D("30000")) == D("0.00")


def test_settlement_overspend():
    s = budget.settlement(D("32000"), D("30000"))
    assert (s.liquidated, s.balance_return, s.top_up) == (D("32000.00"), D("0.00"), D("2000.00"))


def test_settlement_underspend():
    s = budget.settlement(D("27500"), D("30000"))
    assert (s.liquidated, s.balance_return, s.top_up) == (D("27500.00"), D("2500.00"), D("0.00"))


def test_settlement_exact():
    s = budget.settlement(D("30000"), D("30000"))
    assert (s.balance_return, s.top_up) == (D("0.00"), D("0.00"))


def test_q2_rounds_half_up():
    assert q2("0.005") == D("0.01")
    assert q2("2.675") == D("2.68")  # a float would give 2.67
    assert q2("10") == D("10.00")
