// Response shapes of the FastAPI backend. Money arrives as strings (exact decimals).

export type Role = 'REQUESTOR' | 'APPROVER' | 'FINANCE' | 'SUPER_ADMIN'
export type ProjectStatus = 'ACTIVE' | 'INACTIVE'
export type LedgerEntryType = 'ALLOCATE' | 'RESERVE' | 'RELEASE' | 'DISBURSE' | 'LIQUIDATE' | 'RETURN' | 'TOPUP'

export interface Page<T> {
  items: T[]
  total: number
  page: number
  size: number
}

export interface Me {
  id: number
  email: string
  full_name: string
  role: Role
  permissions: string[]
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: Me
}

export interface Project {
  id: number
  code: string
  name: string
  description: string | null
  status: ProjectStatus
  allocated: string
  reserved: string
  outstanding: string
  liquidated: string
  available: string
  created_at: string
  updated_at: string
}

export interface Member {
  user_id: number
  email: string
  full_name: string
  added_at: string
}

export interface LedgerEntry {
  id: number
  entry_type: LedgerEntryType
  amount: string
  request_id: number | null
  request_ref: string | null
  actor_id: number | null
  actor_name: string | null
  note: string | null
  created_at: string
}

export interface AuditEntry {
  id: number
  entity: string
  entity_id: number
  action: string
  from_status: string | null
  to_status: string | null
  reason: string | null
  actor_id: number | null
  actor_name: string | null
  subject_label: string | null
  snapshot: Record<string, unknown> | null
  created_at: string
}

export interface AdminUser {
  id: number
  email: string
  full_name: string
  role: Role
  is_active: boolean
  can_finance_ops: boolean
  permissions: string[]
  created_at: string
}

export interface UserBrief {
  id: number
  full_name: string
  email: string
}

export interface Invitation {
  id: number
  email: string
  role: Role
  expires_at: string
  created_at: string
}

export interface InviteResult {
  id: number
  email: string
  role: Role
  expires_at: string
  invite_link: string
}

export interface UpdateUserResult {
  user: AdminUser
  needs_reassignment: string[]
}
