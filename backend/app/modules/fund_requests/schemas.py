"""Request API schemas (phase 3 §3.1): reads, and the write bodies (``version`` required on every write)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, Field, PlainSerializer, StringConstraints, field_validator

from app.core.money import Money, q2

# Money on the wire is always a decimal string with exactly two places.
MoneyOut = Annotated[Decimal, PlainSerializer(lambda v: str(q2(v)), return_type=str, when_used="json")]

Purpose = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
SupportingInfo = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
TxnRef = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Version = Annotated[int, Field(ge=1)]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

class UserRef(BaseModel):
    id: int
    full_name: str
    email: str

    model_config = {"from_attributes": True}


class ProjectRef(BaseModel):
    id: int
    code: str
    name: str
    status: str

    model_config = {"from_attributes": True}


class RequestSummary(BaseModel):
    id: int
    ref_no: str
    status: str
    project: ProjectRef
    requestor: UserRef
    approver: UserRef
    amount_requested: MoneyOut
    amount_approved: MoneyOut | None
    created_at: datetime
    updated_at: datetime


class DisbursementOut(BaseModel):
    id: int
    amount: MoneyOut
    txn_ref: str
    disbursed_on: date
    notes: str | None
    created_by_name: str | None
    created_at: datetime


class RequestDetail(RequestSummary):
    purpose: str
    supporting_info: str | None
    reserved_amount: MoneyOut
    disbursed_total: MoneyOut
    disbursable_amount: MoneyOut
    version: int
    disbursements: list[DisbursementOut]
    allowed_actions: list[str]


class HistoryEntry(BaseModel):
    id: int
    action: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    actor_name: str | None
    snapshot: dict[str, Any] | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

class _RequestFields(BaseModel):
    approver_id: int
    amount_requested: Money
    purpose: Purpose
    supporting_info: SupportingInfo | None = None

    @field_validator("supporting_info")
    @classmethod
    def _blank_info(cls, value: str | None) -> str | None:
        return value or None


class RequestCreate(_RequestFields):
    project_id: int


class RequestResubmit(_RequestFields):
    version: Version


class VersionBody(BaseModel):
    version: Version


class ReasonBody(BaseModel):
    version: Version
    reason: Reason


class ReassignBody(BaseModel):
    version: Version
    approver_id: int


class DisburseBody(BaseModel):
    version: Version
    txn_ref: TxnRef
    disbursed_on: date
    notes: str | None = None

    @field_validator("notes")
    @classmethod
    def _strip_notes(cls, value: str | None) -> str | None:
        return (value or "").strip() or None
