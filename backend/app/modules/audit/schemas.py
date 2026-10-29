from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditLogOut(BaseModel):
    id: int
    entity: str
    entity_id: int
    action: str
    from_status: str | None
    to_status: str | None
    reason: str | None
    actor_id: int | None
    actor_name: str | None = None
    subject_label: str | None = None  # e.g. "PRJ-001 · Rural Health Camp", a user's name, an invited email
    snapshot: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}
