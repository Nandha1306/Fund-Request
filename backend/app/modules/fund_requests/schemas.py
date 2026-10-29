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


class DocumentOut(BaseModel):
    id: int
    original_name: str
    mime_type: str
    size_bytes: int
    expense_item_id: int | None
    uploaded_by_name: str | None
    created_at: datetime


class ExpenseItemOut(BaseModel):
    id: int
    description: str
    amount: MoneyOut
    expense_date: date
    category: str | None
    comment: str | None
    approval_status: str
    approval_reason: str | None
    approved_by_name: str | None
    approved_at: datetime | None
    verification_status: str
    verification_reason: str | None
    verified_by_name: str | None
    verified_at: datetime | None
    editable: bool
    documents: list[DocumentOut]


class LiquidationOut(BaseModel):
    verified_total: MoneyOut
    disbursed_total: MoneyOut
    balance_returned: MoneyOut
    balance_return_ref: str | None
    top_up_amount: MoneyOut
    completed_by_name: str | None
    completed_at: datetime


class RequestDetail(RequestSummary):
    purpose: str
    supporting_info: str | None
    reserved_amount: MoneyOut
    disbursed_total: MoneyOut
    disbursable_amount: MoneyOut
    version: int
    disbursements: list[DisbursementOut]
    allowed_actions: list[str]
    # Stages 4–6 (phase 4 §3.1)
    overspend_justification: str | None
    expense_total: MoneyOut
    approved_total: MoneyOut
    verified_total: MoneyOut
    overage_reserved: MoneyOut
    expense_items: list[ExpenseItemOut]
    unattached_documents: list[DocumentOut]
    liquidation: LiquidationOut | None


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


class LiquidateBody(BaseModel):
    version: Version
    # Required (by the service) only when the verified total is below the money disbursed.
    balance_return_ref: TxnRef | None = None

    @field_validator("balance_return_ref", mode="before")
    @classmethod
    def _blank_ref(cls, value):
        return None if isinstance(value, str) and not value.strip() else value
