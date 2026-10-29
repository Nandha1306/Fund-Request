"""Project API schemas: writes, reads, members and the ledger view."""

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, StringConstraints, computed_field, field_validator, model_validator

from app.core.enums import ProjectStatus
from app.core.money import Allocation
from app.modules.finance import budget

_CODE_RE = re.compile(r"^[A-Z0-9-]{3,20}$")

ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class ProjectCreate(BaseModel):
    code: str
    name: ProjectName
    description: str | None = None
    allocated: Allocation
    status: ProjectStatus = ProjectStatus.ACTIVE

    @field_validator("code", mode="before")
    @classmethod
    def normalise_code(cls, value: object) -> str:
        """Strip, upper-case **then** pattern-check, so `prj-004` is accepted and stored as `PRJ-004`."""
        if not isinstance(value, str):
            raise ValueError("code must be a string")
        code = value.strip().upper()
        if not _CODE_RE.match(code):
            raise ValueError("code must be 3-20 characters of A-Z, 0-9 or '-'")
        return code


class ProjectUpdate(BaseModel):
    """PATCH body.  The service reads ``model_dump(exclude_unset=True)``, so an omitted
    field and an explicit ``null`` are not confused."""

    name: ProjectName | None = None
    description: str | None = None  # an explicit null clears the description
    status: ProjectStatus | None = None
    allocated: Allocation | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_nulls(cls, data: object) -> object:
        if isinstance(data, dict):
            for field in ("name", "status", "allocated"):
                if field in data and data[field] is None:
                    raise ValueError(f"{field} cannot be null; omit it to leave it unchanged")
        return data


class ProjectOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    status: str
    allocated: Decimal
    reserved: Decimal
    outstanding: Decimal
    liquidated: Decimal
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def available(self) -> Decimal:
        return budget.available(self.allocated, self.reserved, self.outstanding, self.liquidated)


class MemberOut(BaseModel):
    user_id: int
    email: str
    full_name: str
    added_at: datetime


class MembersUpdate(BaseModel):
    user_ids: list[int]  # duplicates are removed in the service


class LedgerEntryOut(BaseModel):
    id: int
    entry_type: str
    amount: Decimal
    request_id: int | None
    request_ref: str | None = None
    actor_id: int | None
    actor_name: str | None = None
    note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
