"""In-app notifications of the current user (phase 3 §3.2)."""

from datetime import UTC, datetime

from fastapi import APIRouter, Response
from sqlalchemy import func, select, update

from app.core.db import DbSession
from app.core.errors import NotFound
from app.core.pagination import Page, Pagination, paginate
from app.modules.accounts.deps import CurrentUser
from app.modules.audit.models import Notification
from app.modules.audit.schemas import NotificationOut, UnreadCount

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=Page[NotificationOut])
def list_notifications(
    db: DbSession, user: CurrentUser, params: Pagination, unread_only: bool = False
) -> Page[NotificationOut]:
    """The caller's own notifications, newest first."""
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.desc())
    rows, total = paginate(db, stmt, params)
    return Page[NotificationOut](
        items=[NotificationOut.model_validate(r) for r in rows],
        total=total,
        page=params.page,
        size=params.size,
    )


@router.get("/notifications/unread-count", response_model=UnreadCount)
def unread_count(db: DbSession, user: CurrentUser) -> UnreadCount:
    count = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id, Notification.read_at.is_(None)
        )
    )
    return UnreadCount(count=count or 0)


@router.post("/notifications/read-all", status_code=204)
def mark_all_read(db: DbSession, user: CurrentUser) -> Response:
    db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    db.commit()
    return Response(status_code=204)


@router.post("/notifications/{notification_id}/read", status_code=204)
def mark_read(notification_id: int, db: DbSession, user: CurrentUser) -> Response:
    """404 for somebody else's notification, exactly as for a missing one."""
    notification = db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == user.id)
    )
    if notification is None:
        raise NotFound("Notification not found")
    if notification.read_at is None:  # marking twice keeps the first read time
        notification.read_at = datetime.now(UTC)
    db.commit()
    return Response(status_code=204)
