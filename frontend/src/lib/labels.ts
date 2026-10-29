import type { ApprovalStatus, LedgerEntryType, RequestStatus, Role, VerificationStatus } from '../api/types'

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
  fund_requests: 'Request',
}

export const ACTION_LABEL: Record<string, string> = {
  created: 'Created',
  updated: 'Updated',
  activated: 'Activated',
  deactivated: 'Deactivated',
  members_changed: 'Members changed',
  invited: 'Invited',
  invitation_accepted: 'Joined',
  submitted: 'Submitted',
  resubmitted: 'Resubmitted',
  approved: 'Approved',
  rejected: 'Rejected',
  reassigned: 'Reassigned',
  disbursement_skipped: 'Skipped disbursement',
  returned: 'Returned',
  withdrawn: 'Withdrawn',
  expenses_saved: 'Saved expenses',
  document_uploaded: 'Uploaded a document',
  document_deleted: 'Deleted a document',
  expenses_submitted: 'Submitted expenses',
  item_approved: 'Approved an item',
  item_rejected: 'Rejected an item',
  expenses_sent_back: 'Sent expenses back',
  expenses_approved: 'Approved expenses',
  item_verified: 'Verified an item',
  item_returned: 'Returned an item',
  expenses_returned: 'Returned expenses',
  liquidated: 'Liquidated',
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
  ref_no: 'Reference',
  amount: 'Amount',
  amount_requested: 'Amount requested',
  amount_approved: 'Amount approved',
  reserved_amount: 'Reserved',
  disbursed_total: 'Disbursed',
  purpose: 'Purpose',
  supporting_info: 'Supporting info',
  approver_id: 'Approver',
  approver_name: 'Approver',
  txn_ref: 'Transaction ref',
  disbursed_on: 'Paid on',
  notes: 'Notes',
  total: 'Total',
  expense_total: 'Expenses',
  verified_total: 'Verified',
  balance_returned: 'Balance returned',
  balance_return_ref: 'Return reference',
  top_up_amount: 'Top-up',
  original_name: 'File',
  document_id: 'Document',
  item_id: 'Item',
  expense_item_id: 'Item',
  mime_type: 'File type',
  size_bytes: 'Size (bytes)',
  approved_total: 'Approved',
  rejected_total: 'Rejected',
  returned_total: 'Returned',
}

export const REQUEST_STATUS_LABEL: Record<RequestStatus, string> = {
  PENDING_APPROVAL: 'Awaiting approval',
  RETURNED_FOR_CORRECTION: 'Returned',
  PENDING_DISBURSEMENT: 'Awaiting disbursement',
  PENDING_EXPENSE_SUBMISSION: 'Awaiting expenses',
  PENDING_EXPENSE_APPROVAL: 'Expense review',
  EXPENSE_RETURNED: 'Expenses returned',
  PENDING_VERIFICATION: 'Awaiting verification',
  LIQUIDATED: 'Liquidated',
  WITHDRAWN: 'Withdrawn',
}

export const APPROVAL_STATUS_LABEL: Record<ApprovalStatus, string> = {
  PENDING: 'Pending',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
}

export const VERIFICATION_STATUS_LABEL: Record<VerificationStatus, string> = {
  PENDING: 'Pending',
  VERIFIED: 'Verified',
  RETURNED: 'Returned',
}

export const EXPENSE_CATEGORIES = ['Travel', 'Lodging', 'Meals', 'Supplies', 'Services', 'Equipment', 'Other'] as const

export const REQUEST_STATUS_OPTIONS = (Object.keys(REQUEST_STATUS_LABEL) as RequestStatus[]).map((s) => ({ value: s, label: REQUEST_STATUS_LABEL[s] }))

/** Whose turn it is in each status; null once the request is closed. */
export type RequestTurn = 'requestor' | 'approver' | 'finance' | null

export const REQUEST_TURN: Record<RequestStatus, RequestTurn> = {
  PENDING_APPROVAL: 'approver',
  RETURNED_FOR_CORRECTION: 'requestor',
  PENDING_DISBURSEMENT: 'finance',
  PENDING_EXPENSE_SUBMISSION: 'requestor',
  PENDING_EXPENSE_APPROVAL: 'approver',
  EXPENSE_RETURNED: 'requestor',
  PENDING_VERIFICATION: 'finance',
  LIQUIDATED: null,
  WITHDRAWN: null,
}

/** What the request is waiting for, in plain words ("Waiting for Approver A to approve"). */
export function waitingFor(status: RequestStatus, names: { requestor: string; approver: string }): string {
  switch (status) {
    case 'PENDING_APPROVAL':
      return `Waiting for ${names.approver} to approve or reject.`
    case 'RETURNED_FOR_CORRECTION':
      return `Waiting for ${names.requestor} to correct and resubmit, or withdraw.`
    case 'PENDING_DISBURSEMENT':
      return 'Approved. Waiting for Finance to disburse the funds.'
    case 'PENDING_EXPENSE_SUBMISSION':
      return `Funds paid out. Waiting for ${names.requestor} to submit expenses.`
    case 'PENDING_EXPENSE_APPROVAL':
      return `Waiting for ${names.approver} to review the expenses.`
    case 'EXPENSE_RETURNED':
      return `Expenses returned. Waiting for ${names.requestor} to correct them.`
    case 'PENDING_VERIFICATION':
      return 'Waiting for Finance to verify and liquidate.'
    case 'LIQUIDATED':
      return 'Closed. The request has been liquidated.'
    case 'WITHDRAWN':
      return 'Closed. The requestor withdrew this request.'
  }
}
