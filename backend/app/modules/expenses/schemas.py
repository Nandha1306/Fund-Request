"""Expense API bodies (phase 4 §3.2).  The read shapes (``ExpenseItemOut``, ``DocumentOut``) live with
``RequestDetail`` in ``fund_requests/schemas.py``; ``version`` is required on every write."""

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.core.money import Money
from app.modules.fund_requests.schemas import DocumentOut, Reason, Version

__all__ = ["DocumentOut", "ExpenseItemIn", "ExpensesSave", "ItemReviewBody", "ItemVerifyBody"]

Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Category = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
Comment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
Justification = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


def _blank_to_none(value: str | None) -> str | None:
    return value or None


class ExpenseItemIn(BaseModel):
    id: int | None = None  # absent → a new item
    description: Description
    amount: Money
    expense_date: date
    category: Category | None = None
    comment: Comment | None = None
    document_ids: list[int] = Field(default_factory=list, max_length=50)

    blank_to_none = field_validator("category", "comment")(_blank_to_none)


class ExpensesSave(BaseModel):
    version: Version
    items: list[ExpenseItemIn] = Field(max_length=200)
    # Left unchanged when the field is not sent at all; ``null`` or blank clears it.
    overspend_justification: Justification | None = None

    blank_to_none = field_validator("overspend_justification")(_blank_to_none)


class _DecisionBody(BaseModel):
    version: Version
    reason: Reason | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _blank_reason(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class ItemReviewBody(_DecisionBody):
    decision: Literal["APPROVED", "REJECTED"]

    @model_validator(mode="after")
    def _reason_when_rejected(self):
        if self.decision == "REJECTED" and not self.reason:
            raise ValueError("A reason is required to reject an item")
        return self


class ItemVerifyBody(_DecisionBody):
    decision: Literal["VERIFIED", "RETURNED"]

    @model_validator(mode="after")
    def _reason_when_returned(self):
        if self.decision == "RETURNED" and not self.reason:
            raise ValueError("A reason is required to return an item")
        return self
