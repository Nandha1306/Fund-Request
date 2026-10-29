import type { AuditEntry, RequestStatus, Role } from '../api/types'
import { formatDateTime, money, toNumber as toNum } from './format'
import { ACTION_LABEL, ENTITY_LABEL, FIELD_LABEL, REQUEST_STATUS_LABEL, ROLE_LABEL } from './labels'

export interface FieldChange {
  field: string
  label: string
  before: string
  after: string
}

const MONEY_FIELDS = new Set([
  'allocated', 'amount', 'amount_requested', 'amount_approved', 'reserved_amount', 'disbursed_total',
  'total', 'expense_total', 'verified_total', 'balance_returned', 'top_up_amount',
  'approved_total', 'rejected_total', 'returned_total',
])

const isPlain = (v: unknown) => v === null || ['string', 'number', 'boolean'].includes(typeof v)

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
  // Lists (the items of an expense submission) are left out; the request page shows them in full.
  return Object.entries(snap)
    .filter(([, v]) => isPlain(v))
    .map(([k, v]) => ({ label: FIELD_LABEL[k] ?? k, value: show(k, v) }))
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
  if (entry.from_status && entry.to_status && entry.from_status !== entry.to_status) return `${show('status', entry.from_status)} → ${show('status', entry.to_status)}`
  const changes = fieldChanges(entry)
  if (changes.length) return changes.map((c) => `${c.label}: ${c.before} → ${c.after}`).join(' · ')
  if (entry.action === 'created' && snap?.allocated !== undefined) return `Allocation ${money(String(snap.allocated))}`
  if (entry.action === 'invited' && snap?.email) return `${snap.email} as ${show('role', snap.role)}`
  if (entry.action === 'invitation_accepted' && snap?.email) return `${snap.email} joined as ${show('role', snap.role)}`
  return expenseSummary(entry.action, snap)
}

/** Details of an expense / liquidation / document event, from its snapshot ("Total ₹32,000.00"). */
export function expenseSummary(action: string, snap: Record<string, unknown> | null): string {
  if (!snap) return ''
  const text = (k: string) => (typeof snap[k] === 'string' || typeof snap[k] === 'number' ? String(snap[k]) : null)
  const parts: string[] = []
  if (action === 'document_uploaded' || action === 'document_deleted') {
    if (text('original_name')) parts.push(text('original_name')!)
  } else if (action === 'expenses_submitted' || action === 'expenses_saved') {
    const items = Array.isArray(snap.items) ? snap.items.length : null
    if (items !== null) parts.push(`${items} item${items === 1 ? '' : 's'}`)
    if (text('total')) parts.push(`total ${money(text('total'))}`)
  } else if (action.startsWith('item_')) {
    if (text('description')) parts.push(text('description')!)
    if (text('amount')) parts.push(money(text('amount')))
  } else if (action === 'expenses_sent_back' && text('rejected_total')) {
    parts.push(`${money(text('rejected_total'))} rejected`)
  } else if (action === 'expenses_approved' && text('approved_total')) {
    parts.push(`${money(text('approved_total'))} approved`)
  } else if (action === 'expenses_returned' && text('returned_total')) {
    parts.push(`${money(text('returned_total'))} returned`)
  } else if (action === 'liquidated') {
    if (text('verified_total')) parts.push(`Verified ${money(text('verified_total'))}`)
    if (toNum(text('balance_returned')) > 0) parts.push(`${money(text('balance_returned'))} returned${text('balance_return_ref') ? ` (ref ${text('balance_return_ref')})` : ''}`)
    if (toNum(text('top_up_amount')) > 0) parts.push(`${money(text('top_up_amount'))} top-up`)
  }
  return parts.join(' · ')
}

/** Where an audit row's subject lives in the app, when it has a page of its own. */
export function subjectLink(entry: Pick<AuditEntry, 'entity' | 'entity_id'>): string | null {
  if (entry.entity === 'projects') return `/projects/${entry.entity_id}`
  if (entry.entity === 'fund_requests') return `/requests/${entry.entity_id}`
  return null
}
