import { LoadingOutlined, PaperClipOutlined } from '@ant-design/icons'
import { App, type TableColumnsType } from 'antd'
import { useState, type CSSProperties } from 'react'

import { expensesApi } from '../../api/endpoints'
import { toApiError } from '../../api/errors'
import type { DocumentOut, ExpenseItem, RequestDetail, RequestStatus } from '../../api/types'
import { ApprovalStatusBadge, VerificationStatusBadge } from '../../components/Badges'
import { formatDate, money, toNumber } from '../../lib/format'
import type { ActionResult } from './RequestDialogs'

/** The page's `perform`: one versioned write, with the shared 409 / 404 handling. */
export type RunAction = (call: () => Promise<RequestDetail>, success: (d: RequestDetail) => string) => Promise<ActionResult>

/** Statuses from Stage 4 on, where the request has (or is about to have) expenses. */
export const EXPENSE_STAGES: RequestStatus[] = ['PENDING_EXPENSE_SUBMISSION', 'EXPENSE_RETURNED', 'PENDING_EXPENSE_APPROVAL', 'PENDING_VERIFICATION', 'LIQUIDATED']

const DRAFTING: RequestStatus[] = ['PENDING_EXPENSE_SUBMISSION', 'EXPENSE_RETURNED']

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

/**
 * A proof document. Files are fetched through the API client (so the Bearer header is sent) and
 * saved from an object URL, because a plain link would reach the server without the token.
 */
export function DocumentLink({ doc, style }: { doc: DocumentOut; style?: CSSProperties }) {
  const { message } = App.useApp()
  const [busy, setBusy] = useState(false)

  const download = async () => {
    if (busy) return
    setBusy(true)
    try {
      const blob = await expensesApi.download(doc.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = doc.original_name
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (err) {
      message.error(toApiError(err).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <a
      className="doc-link"
      role="button"
      title={`${doc.original_name} · ${fileSize(doc.size_bytes)}`}
      style={style}
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        download()
      }}
    >
      {busy ? <LoadingOutlined /> : <PaperClipOutlined />}
      <span className="doc-name">{doc.original_name}</span>
    </a>
  )
}

export function ProofList({ docs }: { docs: DocumentOut[] }) {
  if (!docs.length) return <span className="proof-missing">No proof</span>
  return (
    <div className="proof-list">
      {docs.map((d) => (
        <DocumentLink key={d.id} doc={d} />
      ))}
    </div>
  )
}

/** Description, category / comment, and the latest reviewer reasons in small muted text. */
export function ItemCell({ item }: { item: ExpenseItem }) {
  const sub = [item.category, item.comment].filter(Boolean).join(' · ')
  return (
    <div style={{ minWidth: 0 }}>
      <div className="prewrap">{item.description}</div>
      {sub && <div className="cell-sub prewrap">{sub}</div>}
      {item.approval_status === 'REJECTED' && item.approval_reason && (
        <div className="item-reason">
          <span className="item-reason-who">Rejected{item.approved_by_name ? ` by ${item.approved_by_name}` : ''}:</span> {item.approval_reason}
        </div>
      )}
      {item.verification_status === 'RETURNED' && item.verification_reason && (
        <div className="item-reason">
          <span className="item-reason-who">Returned{item.verified_by_name ? ` by ${item.verified_by_name}` : ''}:</span> {item.verification_reason}
        </div>
      )}
    </div>
  )
}

/** Approval column. A pending decision on an item that hasn't been submitted yet reads as "—". */
function ApprovalCell({ item, status }: { item: ExpenseItem; status: RequestStatus }) {
  if (item.approval_status === 'PENDING' && DRAFTING.includes(status)) return <span className="subtle">—</span>
  return <ApprovalStatusBadge status={item.approval_status} />
}

function VerificationCell({ item, status }: { item: ExpenseItem; status: RequestStatus }) {
  if (item.verification_status === 'PENDING' && status !== 'PENDING_VERIFICATION') return <span className="subtle">—</span>
  return <VerificationStatusBadge status={item.verification_status} />
}

/** The columns every expense table shares (read-only, editor, review and verification). */
export function itemColumns<T extends ExpenseItem>(status: RequestStatus): TableColumnsType<T> {
  return [
    { title: 'Date', width: 112, render: (_, i) => <span className="num">{i.expense_date ? formatDate(i.expense_date) : '—'}</span> },
    { title: 'Item', render: (_, i) => <ItemCell item={i} /> },
    { title: 'Amount', align: 'right', width: 130, render: (_, i) => <span className="num">{money(i.amount)}</span> },
    { title: 'Proof', width: 200, render: (_, i) => <ProofList docs={i.documents} /> },
    { title: 'Approval', width: 104, render: (_, i) => <ApprovalCell item={i} status={status} /> },
    { title: 'Verification', width: 112, render: (_, i) => <VerificationCell item={i} status={status} /> },
  ]
}

/** Expenses · Requested · Disbursed · Difference, under the items table. */
export function ExpenseTotals({ total, requested, disbursed, label = 'Expenses' }: { total: number; requested: string; disbursed: string; label?: string }) {
  const diff = total - toNumber(disbursed)
  const diffNote = diff > 0 ? 'Above the amount paid out' : diff < 0 ? 'Below the amount paid out' : 'Matches the amount paid out'
  const cells = [
    { label, value: money(total) },
    { label: 'Requested', value: money(requested) },
    { label: 'Disbursed', value: money(disbursed) },
    { label: 'Difference', value: `${diff > 0 ? '+' : diff < 0 ? '−' : ''}${money(Math.abs(diff))}`, note: diffNote, color: diff > 0 ? 'var(--warning)' : undefined },
  ]
  return (
    <div className="expense-totals">
      {cells.map((c) => (
        <div key={c.label}>
          <div className="figure-label">{c.label}</div>
          <div className="expense-total-value" style={{ color: c.color }}>{c.value}</div>
          {c.note && <div className="figure-note">{c.note}</div>}
        </div>
      ))}
    </div>
  )
}
