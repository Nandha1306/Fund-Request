"""Domain enumerations.

All enums use StrEnum so values serialise as plain strings in JSON and
in the database (stored as VARCHAR + CHECK constraint, never as a native
PostgreSQL enum type — makes future additions a one-line migration).
"""

import enum


class Role(enum.StrEnum):
    REQUESTOR = "REQUESTOR"
    APPROVER = "APPROVER"
    FINANCE = "FINANCE"
    SUPER_ADMIN = "SUPER_ADMIN"


class ProjectStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class RequestStatus(enum.StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    RETURNED_FOR_CORRECTION = "RETURNED_FOR_CORRECTION"
    PENDING_DISBURSEMENT = "PENDING_DISBURSEMENT"
    PENDING_EXPENSE_SUBMISSION = "PENDING_EXPENSE_SUBMISSION"
    PENDING_EXPENSE_APPROVAL = "PENDING_EXPENSE_APPROVAL"
    EXPENSE_RETURNED = "EXPENSE_RETURNED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    LIQUIDATED = "LIQUIDATED"
    WITHDRAWN = "WITHDRAWN"


class ApprovalStatus(enum.StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class VerificationStatus(enum.StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    RETURNED = "RETURNED"


class LedgerEntryType(enum.StrEnum):
    ALLOCATE = "ALLOCATE"
    RESERVE = "RESERVE"
    RELEASE = "RELEASE"
    DISBURSE = "DISBURSE"
    LIQUIDATE = "LIQUIDATE"
    RETURN = "RETURN"
    TOPUP = "TOPUP"


def enum_check(column: str, enum_cls: type[enum.Enum]) -> str:
    """Return a SQL IN expression used to build a CheckConstraint.

    Usage inside a model::

        CheckConstraint(enum_check("role", Role), name="role_valid")
    """
    values = ", ".join(f"'{m.value}'" for m in enum_cls)
    return f"{column} IN ({values})"
