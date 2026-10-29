import { Tag } from 'antd'

import type { LedgerEntryType, ProjectStatus, Role } from '../api/types'
import { LEDGER_LABEL, ROLE_LABEL } from '../lib/labels'
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

export function CodePill({ children }: { children: string }) {
  return <span className="code-pill">{children}</span>
}
