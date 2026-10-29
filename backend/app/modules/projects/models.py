"""SQLAlchemy models: projects and project_members."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import ProjectStatus, enum_check


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(enum_check("status", ProjectStatus), name="status_valid"),
        CheckConstraint(
            "allocated >= 0 AND reserved >= 0 AND outstanding >= 0 AND liquidated >= 0",
            name="buckets_non_negative",
        ),
        CheckConstraint(
            "reserved + outstanding + liquidated <= allocated",
            name="commitments_within_budget",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    allocated: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    reserved: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    outstanding: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    liquidated: Mapped[object] = mapped_column(Numeric(14, 2), nullable=False, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members"),
    )

    # Composite primary key: one row per (project, user).
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    added_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
