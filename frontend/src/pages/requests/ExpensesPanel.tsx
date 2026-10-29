import { App, Button, Table, type TableColumnsType } from 'antd'
import { useState, type CSSProperties } from 'react'

import { expensesApi } from '../../api/endpoints'
import type { ExpenseItem, RequestDetail } from '../../api/types'
import { Panel } from '../../components/Panel'
import { EmptyState } from '../../components/States'
import { formatDateTime, money, toNumber } from '../../lib/format'
import { ExpenseEditor } from './ExpenseEditor'
import { ExpenseTotals, itemColumns, plural, type RunAction } from './ExpenseParts'
import { LiquidateModal, ReasonModal } from './RequestDialogs'

type Mode = 'view' | 'review' | 'verify'

interface Props {
  r: RequestDetail
  run: RunAction
  /** A write is running or the page is stale: every button is disabled. */
  disabled: boolean
}

/**
 * The Expenses section of the request page, from Stage 4 on. The requestor edits a draft
 * (ExpenseEditor); the approver reviews each item; Finance verifies each item and liquidates.
 * Everyone else sees the items read-only.
 */
export function ExpensesPanel({ r, run, disabled }: Props) {
  if (r.allowed_actions.includes('edit_expenses')) return <ExpenseEditor r={r} run={run} disabled={disabled} />
  return <ReviewPanel r={r} run={run} disabled={disabled} />
}

function ReviewPanel({ r, run, disabled }: Props) {
  const { modal, message } = App.useApp()
  // The item stays set while the dialog closes, so its text doesn't change during the animation.
  const [reasonFor, setReasonFor] = useState<ExpenseItem | null>(null)
  const [reasonOpen, setReasonOpen] = useState(false)
  const askReason = (item: ExpenseItem) => {
    setReasonFor(item)
    setReasonOpen(true)
  }
  const [liquidating, setLiquidating] = useState(false)
  const [busyItem, setBusyItem] = useState<number | null>(null)
  const can = (a: RequestDetail['allowed_actions'][number]) => r.allowed_actions.includes(a)

  const mode: Mode =
    can('review_items') || can('send_back_expenses') || can('approve_expenses')
      ? 'review'
      : can('verify_items') || can('return_expenses') || can('liquidate')
        ? 'verify'
        : 'view'

  const id = r.id
  const v = r.version
  const items = r.expense_items
  const count = (pred: (i: ExpenseItem) => boolean) => items.filter(pred).length

  const decide = async (item: ExpenseItem, call: () => Promise<RequestDetail>, success: string) => {
    setBusyItem(item.id)
    const res = await run(call, () => success)
    setBusyItem(null)
    if (!res.ok && res.error) message.error(res.error.message)
  }

  const confirm = (opts: { title: string; content: string; okText: string; danger?: boolean; call: () => Promise<RequestDetail>; success: string }) =>
    modal.confirm({
      title: opts.title,
      content: opts.content,
      okText: opts.okText,
      okButtonProps: { danger: opts.danger },
      onOk: async () => {
        const res = await run(opts.call, () => opts.success)
        if (!res.ok && res.error) message.error(res.error.message)
      },
    })

  const actionColumn: TableColumnsType<ExpenseItem> =
    mode === 'review' && can('review_items')
      ? [
          {
            title: '',
            width: 156,
            align: 'right',
            render: (_, i) =>
              i.approval_status === 'APPROVED' ? null : (
                <div className="row-actions">
                  <Button
                    size="small"
                    loading={busyItem === i.id}
                    disabled={disabled}
                    onClick={() => decide(i, () => expensesApi.review(id, i.id, v, 'APPROVED'), `"${i.description}" approved`)}
                  >
                    Approve
                  </Button>
                  {i.approval_status === 'PENDING' && (
                    <Button size="small" danger disabled={disabled} onClick={() => askReason(i)}>
                      Reject
                    </Button>
                  )}
                </div>
              ),
          },
        ]
      : mode === 'verify' && can('verify_items')
        ? [
            {
              title: '',
              width: 156,
              align: 'right',
              render: (_, i) =>
                i.verification_status === 'VERIFIED' ? null : (
                  <div className="row-actions">
                    <Button
                      size="small"
                      loading={busyItem === i.id}
                      disabled={disabled}
                      onClick={() => decide(i, () => expensesApi.verify(id, i.id, v, 'VERIFIED'), `"${i.description}" verified`)}
                    >
                      Verify
                    </Button>
                    {i.verification_status === 'PENDING' && (
                      <Button size="small" danger disabled={disabled} onClick={() => askReason(i)}>
                        Return
                      </Button>
                    )}
                  </div>
                ),
            },
          ]
        : []

  const rejected = count((i) => i.approval_status === 'REJECTED')
  const returned = count((i) => i.verification_status === 'RETURNED')
  const progress =
    mode === 'review'
      ? [
          `${count((i) => i.approval_status === 'APPROVED')} of ${items.length} approved`,
          rejected && `${rejected} rejected`,
          count((i) => i.approval_status === 'PENDING') && `${count((i) => i.approval_status === 'PENDING')} to review`,
        ]
      : [
          `${count((i) => i.verification_status === 'VERIFIED')} of ${items.length} verified`,
          returned && `${returned} returned`,
          count((i) => i.verification_status === 'PENDING') && `${count((i) => i.verification_status === 'PENDING')} to check`,
        ]

  const subtitle =
    mode === 'review'
      ? 'Approve or reject each item. Rejected items go back to the requestor with your reason.'
      : mode === 'verify'
        ? 'Check each item against its proof. Returned items go back to the requestor and through approval again.'
        : r.status === 'LIQUIDATED'
          ? 'Verified and settled'
          : items.length
            ? plural(items.length, 'item')
            : undefined

  const footer = mode !== 'view'

  return (
    <Panel title="Expenses" subtitle={subtitle} flush className="panel-gap">
      <Table<ExpenseItem>
        size="small"
        rowKey="id"
        dataSource={items}
        pagination={false}
        locale={{
          emptyText: (
            <EmptyState
              title="No expenses yet"
              description={`${r.requestor.full_name} hasn't submitted any expenses against this request.`}
            />
          ),
        }}
        columns={[...itemColumns<ExpenseItem>(r.status), ...actionColumn]}
      />
      {items.length > 0 && (
        <ExpenseTotals
          total={toNumber(r.expense_total)}
          requested={r.amount_requested}
          disbursed={r.disbursed_total}
        />
      )}
      {r.overspend_justification && (
        <div className="panel-section">
          <div className="figure-label">Why the spend is above the amount requested</div>
          <div className="prewrap" style={{ marginTop: 4 }}>{r.overspend_justification}</div>
        </div>
      )}
      {footer && (
        <div className="panel-foot">
          <span className="muted">{progress.filter(Boolean).join(' · ')}</span>
          <div className="action-buttons">
            {can('send_back_expenses') && (
              <Button
                danger
                disabled={disabled}
                onClick={() =>
                  confirm({
                    title: `Send the expenses back to ${r.requestor.full_name}?`,
                    content: `${plural(rejected, 'rejected item')} go${rejected === 1 ? 'es' : ''} back for correction with your reasons. Approved items stay approved and can't be changed.`,
                    okText: 'Send back',
                    danger: true,
                    call: () => expensesApi.sendBack(id, v),
                    success: `Expenses on ${r.ref_no} sent back to ${r.requestor.full_name}`,
                  })
                }
              >
                Send back
              </Button>
            )}
            {can('approve_expenses') && (
              <Button
                type="primary"
                disabled={disabled}
                onClick={() =>
                  confirm({
                    title: `Approve the expenses on ${r.ref_no}?`,
                    content: `${items.length === 1 ? 'The item' : `All ${items.length} items`} (${money(r.expense_total)}) ${items.length === 1 ? 'is' : 'are'} approved and the expenses go to Finance for verification.`,
                    okText: 'Approve expenses',
                    call: () => expensesApi.approveAll(id, v),
                    success: `Expenses on ${r.ref_no} approved`,
                  })
                }
              >
                Approve expenses
              </Button>
            )}
            {can('return_expenses') && (
              <Button
                danger
                disabled={disabled}
                onClick={() =>
                  confirm({
                    title: `Return the expenses to ${r.requestor.full_name}?`,
                    content: `${plural(returned, 'returned item')} go${returned === 1 ? 'es' : ''} back for correction. After resubmission they pass ${r.approver.full_name}'s review again before they come back to Finance.`,
                    okText: 'Return to requestor',
                    danger: true,
                    call: () => expensesApi.returnAll(id, v),
                    success: `Expenses on ${r.ref_no} returned to ${r.requestor.full_name}`,
                  })
                }
              >
                Return to requestor
              </Button>
            )}
            {can('liquidate') && (
              <Button type="primary" disabled={disabled} onClick={() => setLiquidating(true)}>
                Liquidate
              </Button>
            )}
          </div>
        </div>
      )}

      <ReasonModal
        open={reasonOpen}
        title={mode === 'review' ? 'Reject expense item' : 'Return expense item'}
        intro={
          reasonFor && (
            <>
              <strong style={{ fontWeight: 500, color: 'var(--ink)' }}>{reasonFor.description}</strong> · {money(reasonFor.amount)}
              <br />
              {mode === 'review'
                ? `When you send the expenses back, ${r.requestor.full_name} sees this reason and corrects the item.`
                : `When you return the expenses, ${r.requestor.full_name} corrects the item and it goes through approval again.`}
            </>
          )
        }
        okText={mode === 'review' ? 'Reject item' : 'Return item'}
        onSubmit={async (reason) => {
          const item = reasonFor!
          const res = await run(
            () => (mode === 'review' ? expensesApi.review(id, item.id, v, 'REJECTED', reason) : expensesApi.verify(id, item.id, v, 'RETURNED', reason)),
            () => `"${item.description}" ${mode === 'review' ? 'rejected' : 'returned'}`,
          )
          if (res.ok || !res.error) setReasonOpen(false)
          return res
        }}
        onClose={() => setReasonOpen(false)}
      />
      <LiquidateModal
        open={liquidating}
        request={r}
        onSubmit={async (ref) => {
          const res = await run(() => expensesApi.liquidate(id, v, ref), (d) => `${d.ref_no} liquidated`)
          if (res.ok || !res.error) setLiquidating(false)
          return res
        }}
        onClose={() => setLiquidating(false)}
      />
    </Panel>
  )
}

/** Settlement of a liquidated request: verified vs paid out, and what was returned or topped up. */
export function LiquidationSummary({ r }: { r: RequestDetail }) {
  const l = r.liquidation
  if (!l) return null
  const returned = toNumber(l.balance_returned)
  const topUp = toNumber(l.top_up_amount)
  const settlement = returned > 0
    ? { label: 'Balance returned', value: money(returned), note: l.balance_return_ref ? `Ref ${l.balance_return_ref}` : 'Returned to the project' }
    : topUp > 0
      ? { label: 'Top-up paid', value: money(topUp), note: `Overspend paid to ${r.requestor.full_name}` }
      : { label: 'Settlement', value: money(0), note: 'Spend matched the amount paid out' }
  const figures = [
    { label: 'Verified expenses', value: money(l.verified_total), note: plural(r.expense_items.length, 'item'), color: 'var(--budget-liquidated)' },
    { label: 'Disbursed', value: money(l.disbursed_total), note: 'Paid out on this request' },
    settlement,
    { label: 'Completed by', value: l.completed_by_name ?? 'System', note: formatDateTime(l.completed_at) },
  ]
  return (
    <Panel title="Liquidation" subtitle="The request is closed and the project budget settled" flush className="panel-gap">
      <div className="figures" style={{ '--cols': 4 } as CSSProperties}>
        {figures.map((f) => (
          <div className="figure" key={f.label}>
            <div className="figure-label">
              {'color' in f && f.color && <i className="swatch" style={{ background: f.color }} />}
              {f.label}
            </div>
            <div className="figure-value">{f.value}</div>
            <div className="figure-note">{f.note}</div>
          </div>
        ))}
      </div>
    </Panel>
  )
}
