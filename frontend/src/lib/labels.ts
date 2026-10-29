import type { LedgerEntryType, Role } from '../api/types'

export const ROLE_LABEL: Record<Role, string> = {
  REQUESTOR: 'Requestor',
  APPROVER: 'Approver',
  FINANCE: 'Finance',
  SUPER_ADMIN: 'Super Admin',
}

export const ROLE_HINT: Record<Role, string> = {
  REQUESTOR: 'Raises fund requests and submits expenses for assigned projects',
  APPROVER: 'Approves requests and reviews expense items assigned to them',
  FINANCE: 'Manages projects and budgets, disburses, verifies and liquidates',
  SUPER_ADMIN: 'Invites users and manages access; money actions only when granted',
}

export const ROLE_OPTIONS = (Object.keys(ROLE_LABEL) as Role[]).map((r) => ({ value: r, label: ROLE_LABEL[r] }))

export const LEDGER_LABEL: Record<LedgerEntryType, string> = {
  ALLOCATE: 'Allocation',
  RESERVE: 'Reserved',
  RELEASE: 'Released',
  DISBURSE: 'Disbursed',
  LIQUIDATE: 'Liquidated',
  RETURN: 'Balance returned',
  TOPUP: 'Top-up',
}

export const ENTITY_LABEL: Record<string, string> = {
  projects: 'Project',
  users: 'User',
  invitations: 'Invitation',
}

export const ACTION_LABEL: Record<string, string> = {
  created: 'Created',
  updated: 'Updated',
  activated: 'Activated',
  deactivated: 'Deactivated',
  members_changed: 'Members changed',
  invited: 'Invited',
  invitation_accepted: 'Joined',
}

export const FIELD_LABEL: Record<string, string> = {
  code: 'Code',
  name: 'Name',
  description: 'Description',
  status: 'Status',
  allocated: 'Allocation',
  role: 'Role',
  is_active: 'Active',
  can_finance_ops: 'Finance operations',
  email: 'Email',
  expires_at: 'Expires',
}
