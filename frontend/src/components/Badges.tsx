import { Tag } from 'antd'

import type { ApprovalStatus, LedgerEntryType, ProjectStatus, RequestStatus, Role, VerificationStatus } from '../api/types'
import { APPROVAL_STATUS_LABEL, LEDGER_LABEL, REQUEST_STATUS_LABEL, ROLE_LABEL, VERIFICATION_STATUS_LABEL } from '../lib/labels'
import { palette } from '../theme'

function Status({ color, label }: { color: string; label: string }) {
  return (
    <span className="status">
      <span className="status-dot" style={{ background: color }} />
      {label}
    </span>
  )
}

export function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
  return status === 'ACTIVE' ? <Status color={palette.success} label="Active" /> : <Status color="#a4adb9" label="Inactive" />
}

export function UserStatusBadge({ active }: { active: boolean }) {
  return active ? <Status color={palette.success} label="Active" /> : <Status color="#a4adb9" label="Deactivated" />
}

const ROLE_COLOR: Record<Role, { bg: string; fg: string }> = {
  SUPER_ADMIN: { bg: '#efe9f7', fg: '#5b3f8c' },
  FINANCE: { bg: '#e7f0f8', fg: '#24547f' },
  APPROVER: { bg: '#e8f4ee', fg: '#2a6a49' },
  REQUESTOR: { bg: '#f1f3f6', fg: '#4a5565' },
}

export function RoleBadge({ role }: { role: Role }) {
  const c = ROLE_COLOR[role]
  return (
    <Tag variant="filled" style={{ background: c.bg, color: c.fg, marginInlineEnd: 0 }}>
      {ROLE_LABEL[role]}
    </Tag>
  )
}

const LEDGER_COLOR: Partial<Record<LedgerEntryType, string>> = {
  ALLOCATE: palette.primary,
  RESERVE: palette.budget.reserved,
  RELEASE: '#8a94a3',
  DISBURSE: palette.budget.outstanding,
  LIQUIDATE: palette.budget.liquidated,
  RETURN: palette.success,
  TOPUP: palette.warning,
}

export function LedgerTypeBadge({ type }: { type: LedgerEntryType }) {
  return <Status color={LEDGER_COLOR[type] ?? palette.subtle} label={LEDGER_LABEL[type]} />
}

// Amber: someone has to decide; blue: money is moving; red: back with the requestor; green/grey: closed.
const REQUEST_COLOR: Record<RequestStatus, string> = {
  PENDING_APPROVAL: palette.warning,
  RETURNED_FOR_CORRECTION: palette.danger,
  PENDING_DISBURSEMENT: palette.primary,
  PENDING_EXPENSE_SUBMISSION: palette.budget.outstanding,
  PENDING_EXPENSE_APPROVAL: palette.warning,
  EXPENSE_RETURNED: palette.danger,
  PENDING_VERIFICATION: palette.primary,
  LIQUIDATED: palette.success,
  WITHDRAWN: '#a4adb9',
}

export function RequestStatusBadge({ status }: { status: RequestStatus }) {
  return <Status color={REQUEST_COLOR[status] ?? palette.subtle} label={REQUEST_STATUS_LABEL[status] ?? status} />
}

// Expense items: amber while a decision is due, green once accepted, red when sent back.
const APPROVAL_COLOR: Record<ApprovalStatus, string> = {
  PENDING: palette.warning,
  APPROVED: palette.success,
  REJECTED: palette.danger,
}

const VERIFICATION_COLOR: Record<VerificationStatus, string> = {
  PENDING: palette.warning,
  VERIFIED: palette.success,
  RETURNED: palette.danger,
}

export function ApprovalStatusBadge({ status }: { status: ApprovalStatus }) {
  return <Status color={APPROVAL_COLOR[status]} label={APPROVAL_STATUS_LABEL[status]} />
}

export function VerificationStatusBadge({ status }: { status: VerificationStatus }) {
  return <Status color={VERIFICATION_COLOR[status]} label={VERIFICATION_STATUS_LABEL[status]} />
}

export function CodePill({ children }: { children: string }) {
  return <span className="code-pill">{children}</span>
}
