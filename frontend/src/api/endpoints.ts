import { isAxiosError } from 'axios'

import { api } from './client'
import type {
  AdminUser,
  ApprovalStatus,
  AuditEntry,
  DocumentOut,
  Invitation,
  InviteResult,
  LedgerEntry,
  Me,
  HistoryEntry,
  Member,
  Notification,
  Page,
  Project,
  ProjectStatus,
  RequestDetail,
  RequestStatus,
  RequestSummary,
  Role,
  TokenResponse,
  UpdateUserResult,
  UserBrief,
  VerificationStatus,
} from './types'

const data = <T>(p: Promise<{ data: T }>) => p.then((r) => r.data)

// Drop empty filters so the URL stays clean and the backend sees only what was chosen.
function params<T extends object>(p: T) {
  return Object.fromEntries(Object.entries(p).filter(([, v]) => v !== undefined && v !== null && v !== ''))
}

export const authApi = {
  login: (email: string, password: string) => data(api.post<TokenResponse>('/auth/login', { email, password })),
  acceptInvite: (token: string, full_name: string, password: string) =>
    data(api.post<TokenResponse>('/auth/accept-invite', { token, full_name, password })),
  me: () => data(api.get<Me>('/me')),
}

export interface ProjectQuery {
  page?: number
  size?: number
  status?: ProjectStatus
  q?: string
}

export interface ProjectInput {
  code?: string
  name?: string
  description?: string | null
  allocated?: string
  status?: ProjectStatus
}

export const projectsApi = {
  list: (q: ProjectQuery) => data(api.get<Page<Project>>('/projects', { params: params(q) })),
  get: (id: number) => data(api.get<Project>(`/projects/${id}`)),
  create: (body: ProjectInput) => data(api.post<Project>('/projects', body)),
  update: (id: number, body: ProjectInput) => data(api.patch<Project>(`/projects/${id}`, body)),
  members: (id: number) => data(api.get<Member[]>(`/projects/${id}/members`)),
  setMembers: (id: number, user_ids: number[]) => data(api.put<Member[]>(`/projects/${id}/members`, { user_ids })),
  ledger: (id: number, page: number, size: number) =>
    data(api.get<Page<LedgerEntry>>(`/projects/${id}/ledger`, { params: { page, size } })),
}

export interface UserQuery {
  page?: number
  size?: number
  role?: Role
  is_active?: boolean
  q?: string
}

export interface UserChanges {
  role?: Role
  is_active?: boolean
  can_finance_ops?: boolean
}

export const usersApi = {
  list: (q: UserQuery) => data(api.get<Page<AdminUser>>('/users', { params: params(q) })),
  update: (id: number, changes: UserChanges) => data(api.patch<UpdateUserResult>(`/users/${id}`, changes)),
  invite: (email: string, role: Role) => data(api.post<InviteResult>('/users/invite', { email, role })),
  invitations: () => data(api.get<Invitation[]>('/users/invitations')),
  requestors: () => data(api.get<UserBrief[]>('/users/requestors')),
  /** Active approvers, without the caller (no self-approval). */
  approvers: () => data(api.get<UserBrief[]>('/users/approvers')),
}

export interface AuditQuery {
  page?: number
  size?: number
  entity?: string
  entity_id?: number
  action?: string
  date_from?: string
  date_to?: string
}

export const auditApi = {
  list: (q: AuditQuery) => data(api.get<Page<AuditEntry>>('/audit', { params: params(q) })),
}

export interface RequestQuery {
  page?: number
  size?: number
  status?: RequestStatus[]
  project_id?: number
  requestor_id?: number
  approver_id?: number
  date_from?: string
  date_to?: string
  q?: string
  /** 'action': only requests waiting on the caller. */
  mine?: 'action'
}

export interface RequestInput {
  project_id: number
  approver_id: number
  amount_requested: string
  purpose: string
  supporting_info?: string | null
}

export interface ResubmitInput {
  version: number
  approver_id: number
  amount_requested: string
  purpose: string
  supporting_info?: string | null
}

export interface DisburseInput {
  version: number
  txn_ref: string
  disbursed_on: string
  notes?: string | null
}

export const requestsApi = {
  // `status` is repeatable: status=A&status=B (no brackets), as FastAPI expects.
  list: (q: RequestQuery) =>
    data(api.get<Page<RequestSummary>>('/requests', { params: params(q), paramsSerializer: { indexes: null } })),
  get: (id: number) => data(api.get<RequestDetail>(`/requests/${id}`)),
  history: (id: number) => data(api.get<HistoryEntry[]>(`/requests/${id}/history`)),
  create: (body: RequestInput) => data(api.post<RequestDetail>('/requests', body)),
  resubmit: (id: number, body: ResubmitInput) => data(api.put<RequestDetail>(`/requests/${id}`, body)),
  approve: (id: number, version: number) => data(api.post<RequestDetail>(`/requests/${id}/approve`, { version })),
  reject: (id: number, version: number, reason: string) => data(api.post<RequestDetail>(`/requests/${id}/reject`, { version, reason })),
  reassign: (id: number, version: number, approver_id: number) =>
    data(api.post<RequestDetail>(`/requests/${id}/reassign`, { version, approver_id })),
  withdraw: (id: number, version: number) => data(api.post<RequestDetail>(`/requests/${id}/withdraw`, { version })),
  /** `idempotencyKey` is created once per disburse dialog, so a retry can never pay twice. */
  disburse: (id: number, body: DisburseInput, idempotencyKey: string) =>
    data(api.post<RequestDetail>(`/requests/${id}/disburse`, body, { headers: { 'Idempotency-Key': idempotencyKey } })),
  return: (id: number, version: number, reason: string) => data(api.post<RequestDetail>(`/requests/${id}/return`, { version, reason })),
}

export interface ExpenseItemInput {
  /** Present for an existing (editable) item; left out to create one. */
  id?: number
  description: string
  amount: string
  expense_date: string
  category?: string | null
  comment?: string | null
  document_ids: number[]
}

export interface SaveExpensesInput {
  version: number
  items: ExpenseItemInput[]
  overspend_justification?: string | null
}

/** A blob request's error body arrives as a Blob; turn it back into JSON so toApiError can read it. */
async function readBlobError(err: unknown): Promise<never> {
  if (isAxiosError(err) && err.response?.data instanceof Blob) {
    try {
      err.response.data = JSON.parse(await err.response.data.text())
    } catch {
      err.response.data = undefined
    }
  }
  throw err
}

export const expensesApi = {
  /** Multipart upload. Not versioned: an upload does not change the request. */
  upload: (requestId: number, file: File, expenseItemId?: number) => {
    const form = new FormData()
    form.append('file', file)
    if (expenseItemId !== undefined) form.append('expense_item_id', String(expenseItemId))
    return data(api.post<DocumentOut>(`/requests/${requestId}/documents`, form))
  },
  /** Fetched through the API client (so the Bearer header is sent), returned as a Blob. */
  download: (documentId: number) =>
    api.get<Blob>(`/documents/${documentId}`, { responseType: 'blob' }).then((r) => r.data, readBlobError),
  deleteDocument: (documentId: number) => api.delete(`/documents/${documentId}`).then(() => undefined),
  save: (requestId: number, body: SaveExpensesInput) => data(api.put<RequestDetail>(`/requests/${requestId}/expenses`, body)),
  submit: (requestId: number, version: number) => data(api.post<RequestDetail>(`/requests/${requestId}/expenses/submit`, { version })),
  review: (requestId: number, itemId: number, version: number, decision: Exclude<ApprovalStatus, 'PENDING'>, reason?: string) =>
    data(api.post<RequestDetail>(`/requests/${requestId}/items/${itemId}/review`, { version, decision, reason })),
  sendBack: (requestId: number, version: number) => data(api.post<RequestDetail>(`/requests/${requestId}/expenses/send-back`, { version })),
  approveAll: (requestId: number, version: number) => data(api.post<RequestDetail>(`/requests/${requestId}/expenses/approve`, { version })),
  verify: (requestId: number, itemId: number, version: number, decision: Exclude<VerificationStatus, 'PENDING'>, reason?: string) =>
    data(api.post<RequestDetail>(`/requests/${requestId}/items/${itemId}/verify`, { version, decision, reason })),
  returnAll: (requestId: number, version: number) => data(api.post<RequestDetail>(`/requests/${requestId}/expenses/return`, { version })),
  liquidate: (requestId: number, version: number, balance_return_ref?: string | null) =>
    data(api.post<RequestDetail>(`/requests/${requestId}/liquidate`, { version, balance_return_ref: balance_return_ref ?? undefined })),
}

export const notificationsApi = {
  list: (q: { unread_only?: boolean; page?: number; size?: number }) =>
    data(api.get<Page<Notification>>('/notifications', { params: params(q) })),
  unreadCount: () => data(api.get<{ count: number }>('/notifications/unread-count')),
  markRead: (id: number) => api.post(`/notifications/${id}/read`).then(() => undefined),
  markAllRead: () => api.post('/notifications/read-all').then(() => undefined),
}
