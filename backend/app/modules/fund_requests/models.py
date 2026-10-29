"""SQLAlchemy model: fund_requests."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import RequestStatus, enum_check


class FundRequest(Base):
    __tablename__ = "fund_requests"
    __table_args__ = (
        CheckConstraint(enum_check("status", RequestStatus), name="status_valid"),
        CheckConstraint("amount_requested > 0", name="amount_requested_positive"),
        CheckConstraint(
            "amount_approved IS NULL OR amount_approved > 0",
            name="amount_approved_positive",
        ),
        CheckConstraint(
            "reserved_amount >= 0 AND overage_reserved >= 0",
            name="reserved_non_negative",
        ),
        CheckConstraint("requestor_id <> approver_id", name="no_self_approval"),
        # Indexes improve workflow queries
        Index("ix_fund_requests_status_project", "status", "project_id"),
        Index("ix_fund_requests_requestor", "requestor_id"),
        Index("ix_fund_requests_approver", "approver_id"),
        Index("ix_fund_requests_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # ref_no generation (FR-YYYY-NNNN) is a Phase 3 task; the column is just unique here.
    ref_no: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    requestor_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approver_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    amount_requested: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False)
    amount_approved: Mapped[object | None] = mapped_column(Numeric(14, 2), nullable=True)
    reserved_amount: Mapped[object] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, server_default="0"
    )
    overage_reserved: Mapped[object] = mapped_column(
        Numeric(14, 2), nullable=False, default=0, server_default="0"
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    overspend_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
