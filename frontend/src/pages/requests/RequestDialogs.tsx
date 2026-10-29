import { useQuery } from '@tanstack/react-query'
import { Alert, DatePicker, Form, Input, Modal, Select } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect, useState, type ReactNode } from 'react'

import { usersApi } from '../../api/endpoints'
import { fieldErrors, type ApiError } from '../../api/errors'
import type { RequestDetail } from '../../api/types'
import { money, toNumber } from '../../lib/format'

/**
 * Result of a request write, as the page reports it back to a dialog. A stale-version 409 is
 * handled by the page itself (it closes the dialog and shows the "Reload" state), so dialogs
 * only see the errors they should display.
 */
export type ActionResult = { ok: true } | { ok: false; error: ApiError | null }

// ---------- Reject / Return: a mandatory reason ----------

interface ReasonProps {
  open: boolean
  title: string
  intro: ReactNode
  okText: string
  onSubmit: (reason: string) => Promise<ActionResult>
  onClose: () => void
}

export function ReasonModal({ open, title, intro, okText, onSubmit, onClose }: ReasonProps) {
  const [form] = Form.useForm<{ reason: string }>()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) {
      form.resetFields()
      setError(null)
    }
  }, [open, form])

  const submit = async ({ reason }: { reason: string }) => {
    setBusy(true)
    setError(null)
    const res = await onSubmit(reason.trim())
    setBusy(false)
    if (res.ok || !res.error) return
    const fields = fieldErrors(res.error)
    if (fields.reason) form.setFields([{ name: 'reason', errors: [fields.reason] }])
    else setError(res.error.message)
  }

  return (
    <Modal
      open={open}
      title={title}
      okText={okText}
      okButtonProps={{ danger: true }}
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={busy}
      forceRender // keep the Form mounted so it can be reset before the modal opens
      width={520}
    >
      <p className="muted" style={{ marginTop: 0 }}>{intro}</p>
      {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 12 }} />}
      <Form form={form} layout="vertical" requiredMark={false} onFinish={submit}>
        <Form.Item
          name="reason"
          label="Reason"
          extra="The requestor sees this reason and it is kept in the request history."
          style={{ marginBottom: 0 }}
          rules={[
            { required: true, whitespace: true, message: 'Give a reason' },
            { max: 1000, message: 'Keep it under 1,000 characters' },
          ]}
        >
          <Input.TextArea rows={4} showCount maxLength={1000} placeholder="What needs to change before this can go ahead" />
        </Form.Item>
      </Form>
    </Modal>
  )
}

// ---------- Reassign approver ----------

interface ReassignProps {
  open: boolean
  request: RequestDetail
  onSubmit: (approverId: number, approverName: string) => Promise<ActionResult>
  onClose: () => void
}

export function ReassignModal({ open, request, onSubmit, onClose }: ReassignProps) {
  const [form] = Form.useForm<{ approver_id: number }>()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const approvers = useQuery({ queryKey: ['approvers'], queryFn: usersApi.approvers, enabled: open })
  // The new approver must differ from the current one and from the requestor (no self-approval).
  const options = (approvers.data ?? [])
    .filter((u) => u.id !== request.approver.id && u.id !== request.requestor.id)
    .map((u) => ({ value: u.id, label: u.full_name, email: u.email }))

  useEffect(() => {
    if (open) {
      form.resetFields()
      setError(null)
    }
  }, [open, form])

  const submit = async ({ approver_id }: { approver_id: number }) => {
    setBusy(true)
    setError(null)
    const res = await onSubmit(approver_id, options.find((o) => o.value === approver_id)?.label ?? 'the new approver')
    setBusy(false)
    if (!res.ok && res.error) setError(res.error.message)
  }

  return (
    <Modal
      open={open}
      title={`Reassign ${request.ref_no}`}
      okText="Reassign"
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={busy}
      forceRender
      width={480}
    >
      <p className="muted" style={{ marginTop: 0 }}>
        Currently with <strong style={{ fontWeight: 500, color: 'var(--ink)' }}>{request.approver.full_name}</strong>. Both approvers are notified.
      </p>
      {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 12 }} />}
      <Form form={form} layout="vertical" requiredMark={false} onFinish={submit}>
        <Form.Item name="approver_id" label="New approver" rules={[{ required: true, message: 'Choose an approver' }]} style={{ marginBottom: 0 }}>
          <Select<number>
            placeholder="Choose an approver"
            loading={approvers.isPending}
            showSearch={{ optionFilterProp: 'label' }}
            options={options}
            notFoundContent={approvers.isPending ? undefined : 'No other active approvers'}
            optionRender={(o) => (
              <div>
                <div>{o.data.label}</div>
                <div className="cell-sub">{o.data.email}</div>
              </div>
            )}
          />
        </Form.Item>
      </Form>
    </Modal>
  )
}

// ---------- Disburse ----------

interface DisburseValues {
  txn_ref: string
  disbursed_on: Dayjs
  notes?: string
}

const DISBURSE_FIELDS: (keyof DisburseValues)[] = ['txn_ref', 'disbursed_on', 'notes']

export interface DisburseBody {
  txn_ref: string
  disbursed_on: string
  notes: string | null
}

interface DisburseProps {
  open: boolean
  request: RequestDetail
  /** Called with the same idempotency key for every retry from one opening of the dialog. */
  onSubmit: (body: DisburseBody, idempotencyKey: string) => Promise<ActionResult>
  onClose: () => void
}

export function DisburseModal({ open, request, onSubmit, onClose }: DisburseProps) {
  const [form] = Form.useForm<DisburseValues>()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [key, setKey] = useState('')
  const paidBefore = toNumber(request.disbursed_total)

  useEffect(() => {
    if (open) {
      setKey(crypto.randomUUID()) // once per opening; retries reuse it, so a payment is never recorded twice
      form.resetFields()
      form.setFieldsValue({ disbursed_on: dayjs() })
      setError(null)
    }
  }, [open, form])

  const submit = async (v: DisburseValues) => {
    setBusy(true)
    setError(null)
    const res = await onSubmit(
      { txn_ref: v.txn_ref.trim(), disbursed_on: v.disbursed_on.format('YYYY-MM-DD'), notes: v.notes?.trim() ? v.notes.trim() : null },
      key,
    )
    setBusy(false)
    if (res.ok || !res.error) return
    const e = res.error
    // Only body fields shown in this form (a header error such as a bad Idempotency-Key goes to the alert).
    const fields = Object.entries(fieldErrors(e)).filter(([name]) => DISBURSE_FIELDS.includes(name as keyof DisburseValues))
    if (fields.length) {
      form.setFields(fields.map(([name, msg]) => ({ name: name as keyof DisburseValues, errors: [msg] })))
    } else if (e.code === 'duplicate_txn_ref') {
      form.setFields([{ name: 'txn_ref', errors: [e.message || 'This transaction reference is already recorded'] }])
    } else if (e.status === 422 && /date|future/i.test(e.message)) {
      form.setFields([{ name: 'disbursed_on', errors: [e.message] }])
    } else {
      setError(e.message)
    }
  }

  return (
    <Modal
      open={open}
      title={`Disburse ${request.ref_no}`}
      okText={`Record payment of ${money(request.disbursable_amount)}`}
      onOk={() => form.submit()}
      onCancel={onClose}
      confirmLoading={busy}
      forceRender
      width={520}
    >
      <p className="muted" style={{ marginTop: 0 }}>
        Record the payment made to {request.requestor.full_name}. The amount moves from reserved to disbursed on {request.project.code}.
      </p>
      {error && <Alert type="error" showIcon title={error} style={{ marginBottom: 12 }} />}
      <div className="amount-readonly" style={{ marginBottom: 16 }}>
        <div className="figure-label">Amount to pay</div>
        <div className="figure-value">{money(request.disbursable_amount)}</div>
        <div className="figure-note">
          {paidBefore > 0
            ? `Approved ${money(request.amount_approved)} less ${money(paidBefore)} paid earlier`
            : `The approved amount. It can't be changed here.`}
        </div>
      </div>
      <Form form={form} layout="vertical" requiredMark={false} onFinish={submit}>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 180px', gap: 12 }}>
          <Form.Item
            name="txn_ref"
            label="Transaction reference"
            rules={[
              { required: true, whitespace: true, message: 'Enter the bank or UPI reference' },
              { max: 100, message: 'Keep it under 100 characters' },
            ]}
          >
            <Input placeholder="e.g. NEFT-88231" className="mono" />
          </Form.Item>
          <Form.Item name="disbursed_on" label="Paid on" rules={[{ required: true, message: 'Choose the payment date' }]}>
            <DatePicker style={{ width: '100%' }} format="DD MMM YYYY" disabledDate={(d) => d.isAfter(dayjs(), 'day')} allowClear={false} />
          </Form.Item>
        </div>
        <Form.Item name="notes" label={<>Notes <span className="subtle" style={{ marginLeft: 4 }}>(optional)</span></>} style={{ marginBottom: 0 }}>
          <Input.TextArea rows={2} placeholder="Anything Finance should know about this payment" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
