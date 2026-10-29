"""Demo seed data for local development.

Run with:  python -m app.seed

Rules:
- Refuses to run outside environment == "local" (hard guard against prod accidents).
- Idempotent: users by email, projects by code, members by (project, user).
- Ledger and audit rows are written ONLY when a project is first created.
- Prints a summary line for each object: "created" or "already present".
"""

import sys
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.enums import LedgerEntryType, ProjectStatus, Role
from app.core.security import hash_password
from app.modules.accounts.models import User
from app.modules.audit.models import AuditLog
from app.modules.finance.models import BudgetLedger
from app.modules.projects.models import Project, ProjectMember

# ---------------------------------------------------------------------------
# Seed definitions
# ---------------------------------------------------------------------------

_PASSWORD_HASH = hash_password("Demo@1234")

_USERS = [
    dict(email="admin@demo.local", full_name="Admin User",
         role=Role.SUPER_ADMIN, can_finance_ops=False),
    dict(email="finance@demo.local", full_name="Finance Officer",
         role=Role.FINANCE, can_finance_ops=False),
    dict(email="approver.a@demo.local", full_name="Approver A", role=Role.APPROVER),
    dict(email="approver.b@demo.local", full_name="Approver B", role=Role.APPROVER),
    dict(email="req.a@demo.local", full_name="Requestor A", role=Role.REQUESTOR),
    dict(email="req.b@demo.local", full_name="Requestor B", role=Role.REQUESTOR),
]

_PROJECTS = [
    dict(
        code="PRJ-001", name="Rural Health Camp",
        allocated=Decimal("500000.00"), status=ProjectStatus.ACTIVE,
    ),
    dict(
        code="PRJ-002", name="School Library Upgrade",
        allocated=Decimal("200000.00"), status=ProjectStatus.ACTIVE,
    ),
    dict(
        code="PRJ-003", name="Legacy Pilot",
        allocated=Decimal("100000.00"), status=ProjectStatus.INACTIVE,
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_create_user(db: Session, **kwargs) -> tuple[User, bool]:
    email = kwargs["email"]
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user:
        return user, False
    user = User(
        email=email,
        full_name=kwargs["full_name"],
        password_hash=_PASSWORD_HASH,
        role=kwargs["role"].value,
        can_finance_ops=kwargs.get("can_finance_ops", False),
    )
    db.add(user)
    db.flush()
    return user, True


def _get_or_create_project(
    db: Session, finance_user: User, **kwargs
) -> tuple[Project, bool]:
    code = kwargs["code"]
    project = db.execute(select(Project).where(Project.code == code)).scalar_one_or_none()
    if project:
        return project, False

    project = Project(
        code=code,
        name=kwargs["name"],
        allocated=kwargs["allocated"],
        status=kwargs["status"].value,
        created_by=finance_user.id,
    )
    db.add(project)
    db.flush()

    # Ledger: record the initial allocation
    ledger = BudgetLedger(
        project_id=project.id,
        entry_type=LedgerEntryType.ALLOCATE.value,
        amount=kwargs["allocated"],
        actor_id=finance_user.id,
        note="Initial allocation",
    )
    db.add(ledger)

    # Audit: record the creation event
    audit = AuditLog(
        entity="projects",
        entity_id=project.id,
        action="created",
        actor_id=finance_user.id,
        snapshot={
            "code": code,
            "name": kwargs["name"],
            "allocated": str(kwargs["allocated"]),
            "status": kwargs["status"].value,
        },
    )
    db.add(audit)
    db.flush()

    return project, True


def _ensure_member(db: Session, project: Project, user: User, added_by: User) -> bool:
    existing = db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
        )
    ).scalar_one_or_none()
    if existing:
        return False
    db.add(ProjectMember(project_id=project.id, user_id=user.id, added_by=added_by.id))
    db.flush()
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def seed() -> None:
    settings = get_settings()
    if settings.environment != "local":
        print(f"ERROR: seed refused to run in environment={settings.environment!r}. Local only.")
        sys.exit(1)

    db: Session = SessionLocal()
    try:
        print("=== Seeding demo data ===")

        # Users
        created_users: dict[str, User] = {}
        for u in _USERS:
            user, created = _get_or_create_user(db, **u)
            created_users[u["email"]] = user
            print(f"  user {u['email']}: {'created' if created else 'already present'}")

        finance_user = created_users["finance@demo.local"]

        # Projects
        created_projects: dict[str, Project] = {}
        for p in _PROJECTS:
            project, created = _get_or_create_project(db, finance_user, **p)
            created_projects[p["code"]] = project
            print(f"  project {p['code']}: {'created (+ledger +audit)' if created else 'already present'}")

        # Memberships
        memberships = [
            ("PRJ-001", "req.a@demo.local"),
            ("PRJ-002", "req.a@demo.local"),
            ("PRJ-001", "req.b@demo.local"),
        ]
        for code, email in memberships:
            project = created_projects[code]
            user = created_users[email]
            created = _ensure_member(db, project, user, added_by=finance_user)
            print(f"  member {email} → {code}: {'added' if created else 'already present'}")

        db.commit()
        print("=== Done ===")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
