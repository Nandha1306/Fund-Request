"""Project use cases.

Each function owns its transaction and ends with exactly **one** ``db.commit()``, so the
project row, its ledger rows and its audit rows appear together (arch §11.2 #8).
"""

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.enums import ProjectStatus, Role
from app.core.errors import BusinessRuleViolation, Conflict
from app.core.money import ZERO
from app.modules.accounts.models import User
from app.modules.audit import service as audit
from app.modules.finance import budget
from app.modules.projects.models import Project, ProjectMember
from app.modules.projects.schemas import MemberOut, ProjectCreate, ProjectUpdate

_SNAPSHOT_FIELDS = ["code", "name", "description", "status", "allocated"]


def create_project(db: Session, actor: User, data: ProjectCreate) -> Project:
    """Insert the project at ``allocated = 0`` and let the budget engine write the ALLOCATE row."""
    if db.scalar(select(Project.id).where(Project.code == data.code)):
        raise Conflict("Project code already exists")

    project = Project(
        code=data.code,
        name=data.name,
        description=data.description,
        status=ProjectStatus(data.status).value,
        allocated=ZERO,
        created_by=actor.id,
    )
    db.add(project)
    try:
        db.flush()
    except IntegrityError:  # another transaction inserted the same code first
        db.rollback()
        raise Conflict("Project code already exists") from None

    budget.change_allocation(db, project, data.allocated, actor_id=actor.id)
    audit.record(
        db,
        entity="projects",
        entity_id=project.id,
        action="created",
        actor_id=actor.id,
        snapshot=audit.snapshot_of(project, _SNAPSHOT_FIELDS),
    )
    db.commit()
    return project


def update_project(db: Session, actor: User, project_id: int, data: ProjectUpdate) -> Project:
    """Lock the project, apply the changes (money through the budget engine) and audit them.

    Deactivating a project never touches its existing requests or reservations (edge case #15).
    """
    project = budget.lock_project(db, project_id)  # fresh *and* locked
    changes = data.model_dump(exclude_unset=True)
    before = audit.snapshot_of(project, _SNAPSHOT_FIELDS)

    new_status = changes.pop("status", None)
    if "allocated" in changes:
        budget.change_allocation(db, project, changes.pop("allocated"), actor_id=actor.id)
    for field, value in changes.items():  # name, description
        setattr(project, field, value)

    status_changed = new_status is not None and ProjectStatus(new_status) != ProjectStatus(project.status)
    if status_changed:
        project.status = ProjectStatus(new_status).value

    after = audit.snapshot_of(project, _SNAPSHOT_FIELDS)

    if status_changed:
        audit.record(
            db,
            entity="projects",
            entity_id=project.id,
            action="activated" if project.status == ProjectStatus.ACTIVE.value else "deactivated",
            actor_id=actor.id,
            from_status=before["status"],
            to_status=after["status"],
            snapshot={"before": before, "after": after},
        )
    money_or_text_changed = {k: v for k, v in after.items() if k != "status"} != {
        k: v for k, v in before.items() if k != "status"
    }
    if money_or_text_changed:
        audit.record(
            db,
            entity="projects",
            entity_id=project.id,
            action="updated",
            actor_id=actor.id,
            snapshot={"before": before, "after": after},
        )

    db.commit()
    return project


def list_members(db: Session, project_id: int) -> list[MemberOut]:
    rows = db.execute(
        select(ProjectMember.user_id, User.email, User.full_name, ProjectMember.added_at)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project_id)
        .order_by(User.full_name, User.id)
    ).all()
    return [
        MemberOut(user_id=r.user_id, email=r.email, full_name=r.full_name, added_at=r.added_at) for r in rows
    ]


def set_members(db: Session, actor: User, project: Project, user_ids: list[int]) -> list[MemberOut]:
    """Replace the member list.  Members must be **active requestors** (edge case #16: removing a
    member stops their access to new requests, but their history stays visible to them)."""
    wanted = set(user_ids)
    valid = (
        set(
            db.scalars(
                select(User.id).where(
                    User.id.in_(wanted),
                    User.role == Role.REQUESTOR.value,
                    User.is_active.is_(True),
                )
            )
        )
        if wanted
        else set()
    )
    if invalid := sorted(wanted - valid):  # unknown, inactive or not a requestor
        raise BusinessRuleViolation(
            "Only active requestors can be project members",
            details={"invalid_user_ids": invalid},
        )

    existing = set(db.scalars(select(ProjectMember.user_id).where(ProjectMember.project_id == project.id)))
    added, removed = sorted(wanted - existing), sorted(existing - wanted)

    if removed:
        db.execute(
            delete(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id.in_(removed),
            )
        )
    db.add_all(
        ProjectMember(project_id=project.id, user_id=user_id, added_by=actor.id) for user_id in added
    )

    if added or removed:
        audit.record(
            db,
            entity="projects",
            entity_id=project.id,
            action="members_changed",
            actor_id=actor.id,
            snapshot={"added": added, "removed": removed},
        )

    db.commit()
    return list_members(db, project.id)
