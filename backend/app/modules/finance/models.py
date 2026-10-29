"""SQLAlchemy models: disbursements, liquidations, budget_ledger."""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import LedgerEntryType, enum_check


class Disbursement(Base):
    __tablename__ = "disbursements"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("fund_requests.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    txn_ref: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    disbursed_on: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Liquidation(Base):
    __tablename__ = "liquidations"
    __table_args__ = (
        CheckConstraint(
            "verified_total >= 0 AND disbursed_total >= 0 AND "
            "balance_returned >= 0 AND top_up_amount >= 0",
            name="amounts_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # One liquidation per request enforced by the unique constraint on request_id.
    request_id: Mapped[int] = mapped_column(
        ForeignKey("fund_requests.id", ondelete="RESTRICT"), unique=True, nullable=False
    )
    verified_total: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    disbursed_total: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    balance_returned: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    balance_return_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    top_up_amount: Mapped[object] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, server_default="0"
    )
    completed_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BudgetLedger(Base):
    __tablename__ = "budget_ledger"
    __table_args__ = (
        CheckConstraint(enum_check("entry_type", LedgerEntryType), name="entry_type_valid"),
        # ALLOCATE stores the signed change; all other types must be positive.
        CheckConstraint(
            "entry_type = 'ALLOCATE' OR amount > 0",
            name="amount_positive_unless_allocate",
        ),
        Index("ix_budget_ledger_project_created", "project_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("fund_requests.id", ondelete="RESTRICT"), nullable=True
    )
    entry_type: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
