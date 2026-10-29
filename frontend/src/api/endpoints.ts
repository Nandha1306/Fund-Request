import { api } from './client'
import type {
  AdminUser,
  AuditEntry,
  Invitation,
  InviteResult,
  LedgerEntry,
  Me,
  Member,
  Page,
  Project,
  ProjectStatus,
  Role,
  TokenResponse,
  UpdateUserResult,
  UserBrief,
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
