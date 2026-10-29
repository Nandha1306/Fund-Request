import type { AuditEntry, RequestStatus, Role } from '../api/types'
import { formatDateTime, money } from './format'
import { ACTION_LABEL, ENTITY_LABEL, FIELD_LABEL, REQUEST_STATUS_LABEL, ROLE_LABEL } from './labels'

export interface FieldChange {
  field: string
  label: string
  before: string
  after: string
}

const MONEY_FIELDS = new Set(['allocated', 'amount', 'amount_requested', 'amount_approved', 'reserved_amount', 'disbursed_total'])

function show(field: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (MONEY_FIELDS.has(field)) return money(String(value))
  if (field === 'role') return ROLE_LABEL[value as Role] ?? String(value)
  if (field === 'status') {
    if (value === 'ACTIVE') return 'Active'
    if (value === 'INACTIVE') return 'Inactive'
    return REQUEST_STATUS_LABEL[value as RequestStatus] ?? String(value)
  }
  if (field === 'expires_at') return formatDateTime(String(value))
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}

/** Fields that differ between snapshot.before and snapshot.after. */
export function fieldChanges(entry: AuditEntry): FieldChange[] {
  const snap = entry.snapshot as { before?: Record<string, unknown>; after?: Record<string, unknown> } | null
  if (!snap?.before || !snap.after) return []
  return Object.keys(snap.after)
    .filter((k) => JSON.stringify(snap.before![k]) !== JSON.stringify(snap.after![k]))
    .map((k) => ({ field: k, label: FIELD_LABEL[k] ?? k, before: show(k, snap.before![k]), after: show(k, snap.after![k]) }))
}

/** Key/value pairs of a flat snapshot (created / invited rows). */
export function snapshotFields(entry: AuditEntry): { label: string; value: string }[] {
  const snap = entry.snapshot
  if (!snap || 'before' in snap || 'added' in snap) return []
  return Object.entries(snap).map(([k, v]) => ({ label: FIELD_LABEL[k] ?? k, value: show(k, v) }))
}

/** Names added / removed by a members_changed entry (older entries only stored ids). */
export function memberChanges(entry: AuditEntry): { added: string[]; removed: string[] } {
  const s = (entry.snapshot ?? {}) as { added?: number[]; removed?: number[]; added_names?: string[]; removed_names?: string[] }
  const describe = (names: string[] | undefined, ids: number[] | undefined) => names ?? (ids ?? []).map((id) => `user #${id}`)
  return { added: describe(s.added_names, s.added), removed: describe(s.removed_names, s.removed) }
}

export const actionLabel = (entry: Pick<AuditEntry, 'action'>) => ACTION_LABEL[entry.action] ?? entry.action
export const statusLabel = (status: string) => show('status', status)
export const entityLabel = (entry: AuditEntry) => ENTITY_LABEL[entry.entity] ?? entry.entity

/** One readable line for lists ("Allocation ₹50,000.00 → ₹40,000.00", "2 added, 1 removed"). */
export function summarize(entry: AuditEntry): string {
  const snap = entry.snapshot as Record<string, unknown> | null
  if (entry.action === 'members_changed' && snap) {
    const { added, removed } = memberChanges(entry)
    return [added.length && `Added ${added.join(', ')}`, removed.length && `Removed ${removed.join(', ')}`].filter(Boolean).join(' · ') || 'No change'
  }
  if (entry.from_status && entry.to_status) return `${show('status', entry.from_status)} → ${show('status', entry.to_status)}`
  const changes = fieldChanges(entry)
  if (changes.length) return changes.map((c) => `${c.label}: ${c.before} → ${c.after}`).join(' · ')
  if (entry.action === 'created' && snap?.allocated !== undefined) return `Allocation ${money(String(snap.allocated))}`
  if (entry.action === 'invited' && snap?.email) return `${snap.email} as ${show('role', snap.role)}`
  if (entry.action === 'invitation_accepted' && snap?.email) return `${snap.email} joined as ${show('role', snap.role)}`
  return ''
}

/** Where an audit row's subject lives in the app, when it has a page of its own. */
export function subjectLink(entry: Pick<AuditEntry, 'entity' | 'entity_id'>): string | null {
  if (entry.entity === 'projects') return `/projects/${entry.entity_id}`
  if (entry.entity === 'fund_requests') return `/requests/${entry.entity_id}`
  return null
}
