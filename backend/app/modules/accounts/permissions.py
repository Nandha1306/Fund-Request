"""Role → permission mapping.

Design rule (architecture doc §3):
- Finance **always** has disbursement/verification/liquidation actions.
  The `can_finance_ops` flag ONLY applies to Super Admin.
- Super Admin has user management; Finance does not.
"""

from app.core.enums import Role

FINANCE_OPS: frozenset[str] = frozenset({
    "request.disburse",
    "request.return",
    "expense.verify",
    "request.liquidate",
})

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.REQUESTOR: frozenset({
        "request.create",
        "request.resubmit",
        "request.withdraw",
        "expense.submit",
    }),
    Role.APPROVER: frozenset({
        "request.approve",
        "expense.review",
    }),
    Role.FINANCE: frozenset({
        "project.manage",
        "project.members",
        "request.reassign",
        "request.view_all",
        "audit.view",
    }) | FINANCE_OPS,  # Finance always has money actions
    Role.SUPER_ADMIN: frozenset({
        "project.manage",
        "project.members",
        "request.reassign",
        "request.view_all",
        "audit.view",
        "user.manage",
        # finance ops added only when can_finance_ops=True (see permissions_for)
    }),
}


def permissions_for(user) -> frozenset[str]:
    """Return the full permission set for *user* based on role and flag."""
    base = ROLE_PERMISSIONS.get(Role(user.role), frozenset())
    if user.role == Role.SUPER_ADMIN and user.can_finance_ops:
        return base | FINANCE_OPS
    return base
