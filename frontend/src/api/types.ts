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

// ---------- Fund requests (Phase 3: stages 1–3) ----------

export type RequestStatus =
  | 'PENDING_APPROVAL'
  | 'RETURNED_FOR_CORRECTION'
  | 'PENDING_DISBURSEMENT'
  | 'PENDING_EXPENSE_SUBMISSION'
  | 'PENDING_EXPENSE_APPROVAL'
  | 'EXPENSE_RETURNED'
  | 'PENDING_VERIFICATION'
  | 'LIQUIDATED'
  | 'WITHDRAWN'

/** What the current user may do next, computed by the server. The action panel renders only these. */
export type RequestAction =
  | 'resubmit'
  | 'withdraw'
  | 'approve'
  | 'reject'
  | 'reassign'
  | 'disburse'
  | 'return'
  // Stages 4–6 (expenses, review, verification, liquidation)
  | 'edit_expenses'
  | 'submit_expenses'
  | 'review_items'
  | 'send_back_expenses'
  | 'approve_expenses'
  | 'verify_items'
  | 'return_expenses'
  | 'liquidate'

export interface UserRef {
  id: number
  full_name: string
  email: string
}

export interface ProjectRef {
  id: number
  code: string
  name: string
  status: ProjectStatus
}

export interface RequestSummary {
  id: number
  ref_no: string
  status: RequestStatus
  project: ProjectRef
  requestor: UserRef
  approver: UserRef
  amount_requested: string
  amount_approved: string | null
  created_at: string
  updated_at: string
}

export interface Disbursement {
  id: number
  amount: string
  txn_ref: string
  disbursed_on: string
  notes: string | null
  created_by_name: string | null
  created_at: string
}

// ---------- Expenses, verification and liquidation (Phase 4: stages 4–6) ----------

export type ApprovalStatus = 'PENDING' | 'APPROVED' | 'REJECTED'
export type VerificationStatus = 'PENDING' | 'VERIFIED' | 'RETURNED'

export interface DocumentOut {
  id: number
  original_name: string
  mime_type: string
  size_bytes: number
  expense_item_id: number | null
  uploaded_by_name: string | null
  created_at: string
}

export interface ExpenseItem {
  id: number
  description: string
  amount: string
  expense_date: string
  category: string | null
  comment: string | null
  approval_status: ApprovalStatus
  approval_reason: string | null
  approved_by_name: string | null
  approved_at: string | null
  verification_status: VerificationStatus
  verification_reason: string | null
  verified_by_name: string | null
  verified_at: string | null
  /** Approval PENDING or REJECTED, or verification RETURNED. Approved items are locked. */
  editable: boolean
  documents: DocumentOut[]
}

export interface Liquidation {
  verified_total: string
  disbursed_total: string
  balance_returned: string
  balance_return_ref: string | null
  top_up_amount: string
  completed_by_name: string | null
  completed_at: string
}

export interface RequestDetail extends RequestSummary {
  purpose: string
  supporting_info: string | null
  reserved_amount: string
  disbursed_total: string
  disbursable_amount: string
  version: number
  disbursements: Disbursement[]
  allowed_actions: RequestAction[]
  overspend_justification: string | null
  /** Σ all items. */
  expense_total: string
  /** Σ items with approval APPROVED. */
  approved_total: string
  /** Σ items with verification VERIFIED. */
  verified_total: string
  /** Spend above the disbursed amount, held on the project budget until liquidation. */
  overage_reserved: string
  expense_items: ExpenseItem[]
  /** The requestor's uploads not yet linked to an item. */
  unattached_documents: DocumentOut[]
  liquidation: Liquidation | null
}

export interface HistoryEntry {
  id: number
  action: string
  from_status: string | null
  to_status: string | null
  reason: string | null
  actor_name: string | null
  snapshot: Record<string, unknown> | null
  created_at: string
}

export interface Notification {
  id: number
  type: string
  message: string
  request_id: number | null
  read_at: string | null
  created_at: string
}
