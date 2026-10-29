"""Audit and notification writers.

These helpers only add rows to the caller's session and flush; they never commit.
The calling use case commits once, so the audit row succeeds or fails together with the change it records.
"""

from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog, Notification


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value  # bool, int, str and None are already JSON-safe


def snapshot_of(obj: Any, fields: list[str]) -> dict[str, Any]:
    """JSON-safe copy of the given attributes, for before / after audit snapshots."""
    return {f: _json_safe(getattr(obj, f)) for f in fields}


def record(
    db: Session,
    *,
    entity: str,
    entity_id: int,
    action: str,
    actor_id: int | None,
    from_status: str | None = None,
    to_status: str | None = None,
    reason: str | None = None,
    snapshot: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        entity=entity,
        entity_id=entity_id,
        action=action,
        actor_id=actor_id,
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        snapshot=snapshot,
    )
    db.add(row)
    db.flush()
    return row


def notify(
    db: Session,
    *,
    user_ids: Iterable[int],
    type: str,
    message: str,
    request_id: int | None = None,
) -> None:
    """One notification per distinct user."""
    for uid in sorted(set(user_ids)):
        db.add(Notification(user_id=uid, type=type, message=message, request_id=request_id))
    db.flush()
