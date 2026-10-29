"""Project visibility rules.

Invisible record → **404**; visible but not allowed → 403 from the permission check.
That way a caller never learns that a project exists when they are not allowed to see it (D12).
"""

from sqlalchemy import Select, and_, false, select, true
from sqlalchemy.orm import Session

from app.core.enums import ProjectStatus, Role
from app.core.errors import NotFound
from app.modules.accounts.models import User
from app.modules.accounts.permissions import permissions_for
from app.modules.fund_requests.models import FundRequest
from app.modules.projects.models import Project, ProjectMember


def visible_projects_filter(user: User):
    """SQL condition limiting ``select(Project)`` to what *user* may see."""
    if "project.manage" in permissions_for(user):  # Finance and Super Admin: everything
        return true()

    if user.role == Role.REQUESTOR:  # eligible projects only: active **and** a member
        return and_(
            Project.status == ProjectStatus.ACTIVE.value,
            select(ProjectMember.project_id)
            .where(ProjectMember.project_id == Project.id, ProjectMember.user_id == user.id)
            .exists(),
        )

    if user.role == Role.APPROVER:  # projects of requests they are the approver of
        return (
            select(FundRequest.id)
            .where(FundRequest.project_id == Project.id, FundRequest.approver_id == user.id)
            .exists()
        )

    return false()


def apply_visibility(stmt: Select, user: User) -> Select:
    return stmt.where(visible_projects_filter(user))


def get_project_or_404(db: Session, project_id: int) -> Project:
    """Plain existence lookup for endpoints whose permission already covers all projects."""
    project = db.get(Project, project_id)
    if project is None:
        raise NotFound("Project not found")
    return project


def get_visible_project_or_404(db: Session, user: User, project_id: int) -> Project:
    project = db.scalar(select(Project).where(Project.id == project_id, visible_projects_filter(user)))
    if project is None:
        raise NotFound("Project not found")  # never 403 for an invisible project
    return project
