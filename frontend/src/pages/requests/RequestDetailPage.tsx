import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Skeleton, Table } from 'antd'
import { useState, type CSSProperties } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { requestsApi } from '../../api/endpoints'
import { toApiError } from '../../api/errors'
import type { Disbursement, HistoryEntry, RequestAction, RequestDetail } from '../../api/types'
import { CodePill, RequestStatusBadge } from '../../components/Badges'
import { PageHeader } from '../../components/PageHeader'
import { Panel } from '../../components/Panel'
import { StageTracker } from '../../components/StageTracker'
import { EmptyState, ErrorState, NotFound } from '../../components/States'
import { actionLabel, expenseSummary, statusLabel } from '../../lib/audit'
import { formatDate, formatDateTime, money, shortWhen, toNumber } from '../../lib/format'
import { waitingFor } from '../../lib/labels'
import { afterRequestChange } from './cache'
import { EXPENSE_STAGES, plural } from './ExpenseParts'
import { ExpensesPanel, LiquidationSummary } from './ExpensesPanel'
import { DisburseModal, ReasonModal, ReassignModal, type ActionResult, type DisburseBody } from './RequestDialogs'

type Dialog = 'reject' | 'return' | 'reassign' | 'disburse' | null

/** Actions with a button in the Next step panel. Expense actions live in the Expenses section. */
type PanelAction = Extract<RequestAction, 'approve' | 'disburse' | 'resubmit' | 'reject' | 'return' | 'reassign' | 'withdraw'>

// Primary actions first, then the ones that send the request back or elsewhere.
const ACTION_ORDER: PanelAction[] = ['approve', 'disburse', 'resubmit', 'reject', 'return', 'reassign', 'withdraw']

const EXPENSE_ACTIONS: RequestAction[] = ['edit_expenses', 'submit_expenses', 'review_items', 'send_back_expenses', 'approve_expenses', 'verify_items', 'return_expenses', 'liquidate']

function Figures({ r }: { r: RequestDetail }) {
  const payments = r.disbursements.length
  const items = r.expense_items
  const overage = toNumber(r.overage_reserved)
  const verifiedCount = items.filter((i) => i.verification_status === 'VERIFIED').length
  const figures: { label: string; value: string; note: string; color?: string }[] = [
    { label: 'Requested', value: money(r.amount_requested), note: `Raised ${formatDate(r.created_at)}` },
    { label: 'Approved', value: r.amount_approved ? money(r.amount_approved) : '—', note: r.amount_approved ? 'Approved amount' : 'Not approved yet' },
    {
      label: 'Disbursed',
      value: money(r.disbursed_total),
      color: 'var(--budget-outstanding)',
      note: payments ? `${payments} payment${payments === 1 ? '' : 's'}` : 'Nothing paid yet',
    },
    {
      label: 'Reserved',
      value: money(toNumber(r.reserved_amount) + overage),
      color: 'var(--budget-reserved)',
      note: overage > 0 ? 'Overspend held for the top-up' : 'Held on the project budget',
    },
  ]
  // From Stage 4, once there are items: what was spent and how much of it Finance has verified.
  if (items.length) {
    figures.push(
      { label: 'Expenses', value: money(r.expense_total), note: plural(items.length, 'item') },
      {
        label: 'Verified',
        value: money(r.verified_total),
        color: 'var(--budget-liquidated)',
        note: verifiedCount ? `${verifiedCount} of ${items.length} items` : 'Not verified yet',
      },
    )
  }
  return (
    <div className="figures" style={{ '--cols': figures.length } as CSSProperties}>
      {figures.map((f) => (
        <div className="figure" key={f.label}>
          <div className="figure-label">
            {f.color && <i className="swatch" style={{ background: f.color }} />}
            {f.label}
          </div>
          <div className="figure-value">{f.value}</div>
          <div className="figure-note">{f.note}</div>
        </div>
      ))}
    </div>
  )
}

function Details({ r }: { r: RequestDetail }) {
  return (
    <div className="panel-body">
      <dl className="kv">
        <dt>Purpose</dt>
        <dd className="prewrap">{r.purpose}</dd>
        <dt>Supporting info</dt>
        <dd className="prewrap">{r.supporting_info || <span className="subtle">None given</span>}</dd>
        <dt>Project</dt>
        <dd>
          <Link to={`/projects/${r.project.id}`}>{r.project.name}</Link> <CodePill>{r.project.code}</CodePill>
          {r.project.status === 'INACTIVE' && <span className="subtle"> · inactive</span>}
        </dd>
        <dt>Requestor</dt>
        <dd>
          {r.requestor.full_name} <span className="muted">· {r.requestor.email}</span>
        </dd>
        <dt>Approver</dt>
        <dd>
          {r.approver.full_name} <span className="muted">· {r.approver.email}</span>
        </dd>
        <dt>Raised</dt>
        <dd>{formatDateTime(r.created_at)}</dd>
        <dt>Last updated</dt>
        <dd>{formatDateTime(r.updated_at)}</dd>
      </dl>
    </div>
  )
}

function Disbursements({ items }: { items: Disbursement[] }) {
  return (
    <Table<Disbursement>
      size="small"
      rowKey="id"
      dataSource={items}
      pagination={false}
      locale={{ emptyText: <EmptyState title="Nothing paid out yet" description="Payments recorded by Finance appear here." /> }}
      columns={[
        { title: 'Paid on', width: 120, render: (_, d) => <span className="num">{formatDate(d.disbursed_on)}</span> },
        { title: 'Amount', align: 'right', width: 140, render: (_, d) => <span className="num">{money(d.amount)}</span> },
        { title: 'Transaction ref', render: (_, d) => <CodePill>{d.txn_ref}</CodePill> },
        {
          title: 'Recorded by',
          render: (_, d) => (
            <div>
              <div>{d.created_by_name ?? <span className="subtle">System</span>}</div>
              {d.notes && <div className="cell-sub prewrap">{d.notes}</div>}
            </div>
          ),
        },
      ]}
    />
  )
}

/** Extra line under a history row: the status change, or the details of a payment / reassignment. */
function historyDetail(h: HistoryEntry): string {
  const s = h.snapshot ?? {}
  const text = (k: string) => (typeof s[k] === 'string' || typeof s[k] === 'number' ? String(s[k]) : null)
  const parts: string[] = []
  if (h.action === 'reassigned') {
    const from = text('old_approver_name') ?? text('from_approver_name')
    const to = text('new_approver_name') ?? text('to_approver_name')
    if (from && to) parts.push(`${from} → ${to}`)
  }
  if (h.action === 'disbursed') {
    if (text('amount')) parts.push(money(text('amount')))
    if (text('txn_ref')) parts.push(`ref ${text('txn_ref')}`)
  }
  const expense = expenseSummary(h.action, s)
  if (expense) parts.push(expense)
  if (h.from_status && h.to_status && h.from_status !== h.to_status) parts.push(`${statusLabel(h.from_status)} → ${statusLabel(h.to_status)}`)
  else if (!h.from_status && h.to_status) parts.push(statusLabel(h.to_status))
  return parts.join(' · ')
}

function History({ id }: { id: number }) {
  const history = useQuery({ queryKey: ['request', id, 'history'], queryFn: () => requestsApi.history(id) })
  if (history.isError) return <ErrorState error={history.error} onRetry={() => history.refetch()} />
  if (history.isPending) return <Skeleton active paragraph={{ rows: 3 }} style={{ padding: 16 }} />
  if (!history.data.length) return <EmptyState title="No history yet" />
  return (
    <ul className="activity">
      {history.data.map((h) => {
        const detail = historyDetail(h)
        return (
          <li key={h.id}>
            <span className="activity-time" title={formatDateTime(h.created_at)}>{shortWhen(h.created_at)}</span>
            <div className="activity-text" style={{ minWidth: 0, flex: 1 }}>
              <div>
                <strong style={{ fontWeight: 500 }}>{h.actor_name ?? 'System'}</strong> <span className="muted">{actionLabel(h).toLowerCase()}</span>
              </div>
              {detail && <div className="cell-sub">{detail}</div>}
              {h.reason && <div className="reason">{h.reason}</div>}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

export default function RequestDetailPage() {
  const id = Number(useParams().id)
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { modal, message } = App.useApp()
  const [dialog, setDialog] = useState<Dialog>(null)
  const [stale, setStale] = useState(false)
  const request = useQuery({ queryKey: ['request', id], queryFn: () => requestsApi.get(id), enabled: Number.isFinite(id) })

  const write = useMutation({
    mutationFn: (call: () => Promise<RequestDetail>) => call(),
    onSuccess: (detail) => afterRequestChange(queryClient, detail),
  })

  const reload = () => {
    setStale(false)
    queryClient.invalidateQueries({ queryKey: ['request', id] })
  }

  /**
   * Run one write against the version this page was loaded with. A 409 `conflict` means the request
   * moved on (someone else acted, or the version is stale): close the dialog, show the Reload state,
   * refetch. A 409 `duplicate_txn_ref` (reused transaction reference on disburse) is a field error,
   * so it goes back to the dialog. A 404 means the request is no longer visible to this user (for
   * example it was reassigned to another approver): show the same Reload state without refetching,
   * so the explanation stays on screen; Reload then shows the not-found page.
   */
  const perform = async (call: () => Promise<RequestDetail>, success: (d: RequestDetail) => string): Promise<ActionResult> => {
    try {
      const detail = await write.mutateAsync(call)
      message.success(success(detail))
      setDialog(null)
      setStale(false)
      return { ok: true }
    } catch (err) {
      const e = toApiError(err)
      if (e.status === 404) {
        setDialog(null)
        setStale(true)
        return { ok: false, error: null }
      }
      if (e.status !== 409 || e.code === 'duplicate_txn_ref') return { ok: false, error: e }
      await request.refetch()
      queryClient.invalidateQueries({ queryKey: ['request', id, 'history'] })
      setDialog(null)
      setStale(true)
      return { ok: false, error: null }
    }
  }

  if (request.isError) {
    return toApiError(request.error).status === 404 ? <NotFound what="request" /> : <div className="page"><ErrorState error={request.error} onRetry={() => request.refetch()} /></div>
  }
  if (!request.data) return <div className="page"><Skeleton active paragraph={{ rows: 8 }} /></div>

  const r = request.data
  const v = r.version
  const actions = ACTION_ORDER.filter((a) => r.allowed_actions.includes(a))
  const can = (a: RequestAction) => r.allowed_actions.includes(a)
  const expenseTurn = EXPENSE_ACTIONS.some(can)
  const showExpenses = EXPENSE_STAGES.includes(r.status) || r.expense_items.length > 0
  const names = { requestor: r.requestor.full_name, approver: r.approver.full_name }
  const paidBefore = toNumber(r.disbursed_total)
  const skipsDisbursement = paidBefore >= toNumber(r.amount_requested)

  // Confirm dialogs resolve either way, so they always close; errors surface as a toast or the Reload state.
  const confirmAction = (opts: { title: string; content: string; okText: string; danger?: boolean; run: () => Promise<RequestDetail>; success: string }) =>
    modal.confirm({
      title: opts.title,
      content: opts.content,
      okText: opts.okText,
      okButtonProps: { danger: opts.danger },
      onOk: async () => {
        const res = await perform(opts.run, () => opts.success)
        if (!res.ok && res.error) message.error(res.error.message)
      },
    })

  const buttons: Record<PanelAction, () => void> = {
    approve: () =>
      confirmAction({
        title: `Approve ${r.ref_no}?`,
        content: skipsDisbursement
          ? `${money(r.amount_requested)} will be approved. ${money(paidBefore)} was already paid on this request, so it moves straight to expense submission.`
          : `${money(r.amount_requested)} will be approved and Finance will be notified to disburse it.`,
        okText: 'Approve',
        run: () => requestsApi.approve(id, v),
        success: `${r.ref_no} approved`,
      }),
    reject: () => setDialog('reject'),
    reassign: () => setDialog('reassign'),
    disburse: () => setDialog('disburse'),
    return: () => setDialog('return'),
    resubmit: () => navigate(`/requests/${id}/resubmit`),
    withdraw: () =>
      confirmAction({
        title: `Withdraw ${r.ref_no}?`,
        content: `The request is closed and the ${money(r.reserved_amount)} it holds on ${r.project.code} is released. This can't be undone.`,
        okText: 'Withdraw request',
        danger: true,
        run: () => requestsApi.withdraw(id, v),
        success: `${r.ref_no} withdrawn`,
      }),
  }

  const BUTTON: Record<PanelAction, { label: string; primary?: boolean; danger?: boolean }> = {
    approve: { label: 'Approve', primary: true },
    disburse: { label: `Disburse ${money(r.disbursable_amount)}`, primary: true },
    resubmit: { label: 'Correct and resubmit', primary: true },
    reject: { label: 'Reject', danger: true },
    return: { label: 'Return to requestor', danger: true },
    reassign: { label: 'Reassign approver' },
    withdraw: { label: 'Withdraw', danger: true },
  }

  const rejectedItems = r.expense_items.filter((i) => i.approval_status === 'REJECTED').length
  const returnedItems = r.expense_items.filter((i) => i.verification_status === 'RETURNED').length
  const expenseStep = can('edit_expenses')
    ? r.status === 'EXPENSE_RETURNED'
      ? 'Expenses returned to you. Correct the rejected or returned items under Expenses, then submit them again.'
      : 'Add your expenses under Expenses, each with a receipt or invoice, then submit them for review.'
    : can('approve_expenses')
      ? 'Every item is approved. Approve the expenses to send them to Finance for verification.'
      : can('send_back_expenses')
        ? `Every item is reviewed and ${plural(rejectedItems, 'item')} rejected. Send the expenses back to ${r.requestor.full_name} for correction.`
        : can('review_items')
          ? 'Waiting for your review. Approve or reject each expense item under Expenses.'
          : can('liquidate')
            ? 'Every item is verified. Liquidate the request to settle the budget and close it.'
            : can('return_expenses')
              ? `Every item is checked and ${plural(returnedItems, 'item')} returned. Return the expenses to ${r.requestor.full_name} for correction.`
              : can('verify_items')
                ? 'Waiting for verification. Verify or return each expense item under Expenses.'
                : null

  // Shown above the Expenses section from Stage 4 on (where the buttons are), else in Next step.
  const staleAlert = (style: CSSProperties) =>
    stale && (
      <Alert
        type="warning"
        showIcon
        style={style}
        title="This request changed"
        description="Someone else acted on it while you had it open, so your action was not applied. Reload to see where it stands now."
        action={<Button size="small" onClick={reload} loading={request.isFetching}>Reload</Button>}
      />
    )

  const yourTurn = expenseStep
    ? expenseStep
    : actions.includes('approve')
    ? 'Waiting for your decision. Approve it, or reject it with a reason.'
    : actions.includes('resubmit')
      ? actions.includes('withdraw')
        ? 'Returned to you for correction. Correct and resubmit it, or withdraw it.'
        : `Returned to you for correction. Correct and resubmit it. It can't be withdrawn because ${money(r.disbursed_total)} was already paid.`
      : actions.includes('disburse')
        ? `Approved and ready to be paid out: ${money(r.disbursable_amount)}.`
        : waitingFor(r.status, names)

  return (
    <div className="page">
      <PageHeader
        crumbs={[{ label: 'Requests', to: '/requests' }, { label: r.ref_no }]}
        title={
          <>
            {r.ref_no}
            <RequestStatusBadge status={r.status} />
          </>
        }
        description={
          <>
            <Link to={`/projects/${r.project.id}`}>{r.project.name}</Link>
            <span className="subtle"> · </span>
            Raised by {r.requestor.full_name} on {formatDate(r.created_at)}
          </>
        }
      />
      <Panel flush>
        <StageTracker status={r.status} disbursedTotal={r.disbursed_total} />
      </Panel>
      <Panel flush className="panel-gap">
        <Figures r={r} />
      </Panel>
      {showExpenses && staleAlert({ marginTop: 16 })}
      <LiquidationSummary r={r} />
      {showExpenses && <ExpensesPanel r={r} run={perform} disabled={stale || write.isPending} />}

      <div className="detail-grid">
        <div>
          <Panel title="Details" flush>
            <Details r={r} />
          </Panel>
          <Panel title="Disbursements" subtitle="Payments recorded against this request" flush>
            <Disbursements items={r.disbursements} />
          </Panel>
        </div>
        <div>
          <Panel title="Next step">
            {!showExpenses && staleAlert({ marginBottom: 12 })}
            <div>{yourTurn}</div>
            {actions.length > 0 ? (
              <div className="action-buttons" style={{ marginTop: 12 }}>
                {actions.map((a) => (
                  <Button key={a} type={BUTTON[a].primary ? 'primary' : 'default'} danger={BUTTON[a].danger} onClick={buttons[a]} disabled={stale || write.isPending}>
                    {BUTTON[a].label}
                  </Button>
                ))}
              </div>
            ) : expenseTurn ? null : (
              <div className="cell-sub" style={{ marginTop: 6 }}>Nothing for you to do on this request right now.</div>
            )}
          </Panel>
          <Panel title="History" flush>
            <History id={id} />
          </Panel>
        </div>
      </div>

      <ReasonModal
        open={dialog === 'reject'}
        title={`Reject ${r.ref_no}`}
        intro={`The request goes back to ${r.requestor.full_name} for correction, and the ${money(r.reserved_amount)} reserved on ${r.project.code} is released.`}
        okText="Reject request"
        onSubmit={(reason) => perform(() => requestsApi.reject(id, v, reason), (d) => `${d.ref_no} returned to ${d.requestor.full_name}`)}
        onClose={() => setDialog(null)}
      />
      <ReasonModal
        open={dialog === 'return'}
        title={`Return ${r.ref_no} to the requestor`}
        intro={
          paidBefore > 0
            ? `The request goes back to ${r.requestor.full_name} for correction. The ${money(paidBefore)} already paid stays with them; after resubmission only a shortfall can be paid.`
            : `The request goes back to ${r.requestor.full_name} for correction, and the ${money(r.reserved_amount)} reserved on ${r.project.code} is released.`
        }
        okText="Return request"
        onSubmit={(reason) => perform(() => requestsApi.return(id, v, reason), (d) => `${d.ref_no} returned to ${d.requestor.full_name}`)}
        onClose={() => setDialog(null)}
      />
      <ReassignModal
        open={dialog === 'reassign'}
        request={r}
        onSubmit={(approverId, name) => perform(() => requestsApi.reassign(id, v, approverId), (d) => `${d.ref_no} reassigned to ${name}`)}
        onClose={() => setDialog(null)}
      />
      <DisburseModal
        open={dialog === 'disburse'}
        request={r}
        onSubmit={(body: DisburseBody, key) =>
          perform(() => requestsApi.disburse(id, { version: v, ...body }, key), () => `${money(r.disbursable_amount)} disbursed on ${r.ref_no}`)
        }
        onClose={() => setDialog(null)}
      />
    </div>
  )
}
