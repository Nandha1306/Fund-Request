import { CloseOutlined, LockOutlined, PlusOutlined, UploadOutlined } from '@ant-design/icons'
import { useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, DatePicker, Drawer, Form, Input, InputNumber, Popconfirm, Select, Table, Tooltip, Upload, type TableColumnsType } from 'antd'
import dayjs, { type Dayjs } from 'dayjs'
import { useEffect, useMemo, useState } from 'react'

import { expensesApi, type ExpenseItemInput } from '../../api/endpoints'
import { fieldErrors, toApiError, type ApiError } from '../../api/errors'
import type { DocumentOut, ExpenseItem, RequestDetail } from '../../api/types'
import { Panel } from '../../components/Panel'
import { EmptyState } from '../../components/States'
import { groupAmountInput, money, parseAmountInput, toNumber } from '../../lib/format'
import { EXPENSE_CATEGORIES } from '../../lib/labels'
import { afterRequestChange } from './cache'
import { DocumentLink, ExpenseTotals, fileSize, itemColumns, plural, type RunAction } from './ExpenseParts'

/** An editable item as the requestor is shaping it; saved with "Save draft" or on submit. */
interface Draft {
  key: string
  id?: number
  description: string
  amount: string
  expense_date: string
  category: string | null
  comment: string | null
  documents: DocumentOut[]
}

/** One table row: a draft (merged over its server item for the review fields) or a locked item. */
type Row = ExpenseItem & { key: string; locked: boolean }

const AMOUNT_PATTERN = /^\d+(\.\d{1,2})?$/
const ACCEPT = '.pdf,.jpg,.jpeg,.png'

const toDraft = (i: ExpenseItem): Draft => ({
  key: `item-${i.id}`,
  id: i.id,
  description: i.description,
  amount: i.amount,
  expense_date: i.expense_date,
  category: i.category,
  comment: i.comment,
  documents: i.documents,
})

const draftsOf = (r: RequestDetail) => r.expense_items.filter((i) => i.editable).map(toDraft)

/** Comparable form of the drafts, to tell whether anything changed since the last save. */
const fingerprint = (drafts: Draft[], justification: string) =>
  JSON.stringify([
    drafts.map((d) => [d.id ?? null, d.description, toNumber(d.amount).toFixed(2), d.expense_date, d.category, d.comment, d.documents.map((x) => x.id).sort()]),
    justification.trim(),
  ])

const NEW_ITEM: ExpenseItem = {
  id: 0,
  description: '',
  amount: '0',
  expense_date: '',
  category: null,
  comment: null,
  approval_status: 'PENDING',
  approval_reason: null,
  approved_by_name: null,
  approved_at: null,
  verification_status: 'PENDING',
  verification_reason: null,
  verified_by_name: null,
  verified_at: null,
  editable: true,
  documents: [],
}

interface Props {
  r: RequestDetail
  run: RunAction
  disabled: boolean
}

/** Stage 4 for the requestor: add, correct and remove items, attach proof, then submit for review. */
export function ExpenseEditor({ r, run, disabled }: Props) {
  const { modal, message } = App.useApp()
  const queryClient = useQueryClient()
  const [drafts, setDrafts] = useState<Draft[]>(() => draftsOf(r))
  const [justification, setJustification] = useState(r.overspend_justification ?? '')
  const [justificationError, setJustificationError] = useState<string | null>(null)
  const [loadedVersion, setLoadedVersion] = useState(r.version)
  // The drawer's item stays set while it closes, so its title doesn't change during the animation.
  const [editing, setEditing] = useState<Draft | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'save' | 'submit' | null>(null)
  // Files uploaded or deleted while this page is open (uploads don't change the request version).
  const [uploads, setUploads] = useState<DocumentOut[]>([])
  const [deleted, setDeleted] = useState<number[]>([])

  // A new version (our own save, or a reload after a conflict): start again from the server's items.
  if (r.version !== loadedVersion) {
    setLoadedVersion(r.version)
    setDrafts(draftsOf(r))
    setJustification(r.overspend_justification ?? '')
    setJustificationError(null)
  }

  const requested = toNumber(r.amount_requested)
  const disbursed = toNumber(r.disbursed_total)
  const locked = r.expense_items.filter((i) => !i.editable)

  const rows: Row[] = useMemo(() => {
    const byId = new Map(drafts.filter((d) => d.id).map((d) => [d.id!, d]))
    const merged: Row[] = []
    for (const item of r.expense_items) {
      if (!item.editable) merged.push({ ...item, key: `item-${item.id}`, locked: true })
      else if (byId.has(item.id)) merged.push({ ...item, ...byId.get(item.id)!, locked: false })
    }
    for (const d of drafts.filter((x) => !x.id)) merged.push({ ...NEW_ITEM, ...d, id: 0, locked: false })
    return merged
  }, [r.expense_items, drafts])

  const total = rows.reduce((sum, row) => sum + toNumber(row.amount), 0)
  const overspent = total > requested + 0.004
  const overage = Math.max(0, total - disbursed)
  const underspend = Math.max(0, disbursed - total)
  const dirty = fingerprint(drafts, overspent ? justification : '') !== fingerprint(draftsOf(r), overspent ? r.overspend_justification ?? '' : '')

  // Uploads that no item refers to: the requestor can attach them to an item or delete them.
  const referenced = new Set(drafts.flatMap((d) => d.documents.map((x) => x.id)))
  const known = new Map<number, DocumentOut>()
  for (const doc of [...r.unattached_documents, ...r.expense_items.filter((i) => i.editable).flatMap((i) => i.documents), ...uploads]) known.set(doc.id, doc)
  const loose = [...known.values()].filter((doc) => !referenced.has(doc.id) && !deleted.includes(doc.id))

  const body = () => ({
    version: r.version,
    items: drafts.map(
      (d): ExpenseItemInput => ({
        id: d.id,
        description: d.description,
        amount: d.amount,
        expense_date: d.expense_date,
        category: d.category,
        comment: d.comment,
        document_ids: d.documents.map((x) => x.id),
      }),
    ),
    overspend_justification: overspent && justification.trim() ? justification.trim() : null,
  })

  /** Show a save / submit error: a field of one item, the justification, or the whole panel. */
  const showError = (e: ApiError) => {
    if (e.code === 'validation_error' && Array.isArray(e.details)) {
      const issue = (e.details as { loc?: (string | number)[]; msg?: string }[])[0]
      const loc = (issue?.loc ?? []).filter((p) => p !== 'body')
      if (loc[0] === 'overspend_justification') {
        setJustificationError(e.message)
        return
      }
      if (loc[0] === 'items' && typeof loc[1] === 'number') {
        const d = drafts[loc[1]]
        setError(`${d ? `"${d.description}"` : `Item ${loc[1] + 1}`}: ${e.message}`)
        return
      }
    }
    const fields = fieldErrors(e)
    if (fields.overspend_justification) return setJustificationError(fields.overspend_justification)
    const details = e.details as { available?: string; required?: string } | null
    if (e.status === 422 && details?.available !== undefined) {
      setError(
        `The overspend can't be reserved: ${details.required ? `${money(details.required)} is needed but ` : ''}only ${money(details.available)} is available on ${r.project.code}. Reduce the expenses or ask Finance to increase the budget.`,
      )
      return
    }
    if (e.status === 422 && /justif/i.test(e.message)) return setJustificationError(e.message)
    setError(e.message)
  }

  const save = async () => {
    setError(null)
    setBusy('save')
    const res = await run(() => expensesApi.save(r.id, body()), () => 'Expenses saved as a draft')
    setBusy(null)
    if (!res.ok && res.error) showError(res.error)
  }

  const submit = () => {
    setError(null)
    if (!rows.length) return setError('Add at least one expense before submitting.')
    const missing = rows.filter((row) => !row.documents.length)
    if (missing.length) return setError(`Every item needs a proof document. Missing on: ${missing.map((m) => `"${m.description}"`).join(', ')}.`)
    if (overspent && !justification.trim()) return setJustificationError('Explain why the expenses are above the amount requested')

    modal.confirm({
      title: `Submit expenses on ${r.ref_no}?`,
      content: (
        <>
          Send {plural(rows.length, 'item')} totalling {money(total)} to {r.approver.full_name} for review.
          {overage > 0 && ` ${money(overage)} above the amount paid out is reserved on ${r.project.code}'s budget.`}
        </>
      ),
      okText: 'Submit expenses',
      onOk: async () => {
        setBusy('submit')
        const res = await run(async () => {
          if (!dirty) return expensesApi.submit(r.id, r.version)
          const saved = await expensesApi.save(r.id, body())
          try {
            return await expensesApi.submit(r.id, saved.version)
          } catch (err) {
            afterRequestChange(queryClient, saved) // the draft is saved; keep the page on its new version
            throw err
          }
        }, (d) => `Expenses on ${d.ref_no} submitted to ${d.approver.full_name}`)
        setBusy(null)
        if (!res.ok && res.error) showError(res.error)
      },
    })
  }

  const remove = (key: string) => setDrafts((ds) => ds.filter((d) => d.key !== key))
  const openDrawer = (draft: Draft | null) => {
    setEditing(draft)
    setDrawerOpen(true)
  }

  const deleteUpload = async (doc: DocumentOut) => {
    try {
      await expensesApi.deleteDocument(doc.id)
      setDeleted((ids) => [...ids, doc.id])
      queryClient.invalidateQueries({ queryKey: ['request', r.id, 'history'] })
      queryClient.invalidateQueries({ queryKey: ['audit'] })
      message.success(`${doc.original_name} deleted`)
    } catch (err) {
      message.error(toApiError(err).message)
    }
  }

  const columns: TableColumnsType<Row> = [
    ...itemColumns<Row>(r.status),
    {
      title: '',
      width: 128,
      align: 'right',
      render: (_, row) =>
        row.locked ? (
          <Tooltip title={`Approved${row.approved_by_name ? ` by ${row.approved_by_name}` : ''}. Approved items can't be changed.`}>
            <span className="subtle"><LockOutlined /> Locked</span>
          </Tooltip>
        ) : (
          <div className="row-actions">
            <Button size="small" type="link" disabled={disabled} onClick={() => openDrawer(drafts.find((d) => d.key === row.key) ?? null)}>
              Edit
            </Button>
            <Button size="small" type="link" danger disabled={disabled} onClick={() => remove(row.key)}>
              Remove
            </Button>
          </div>
        ),
    },
  ]

  return (
    <Panel
      title="Expenses"
      subtitle="Add each expense with its proof (PDF, JPG or PNG). Save a draft any time and submit when complete."
      extra={
        <Button icon={<PlusOutlined />} onClick={() => openDrawer(null)} disabled={disabled}>
          Add expense
        </Button>
      }
      flush
      className="panel-gap"
    >
      {error && (
        <div style={{ padding: '12px 16px 0' }}>
          <Alert type="error" showIcon title={error} closable={{ onClose: () => setError(null) }} />
        </div>
      )}
      <Table<Row>
        size="small"
        rowKey="key"
        dataSource={rows}
        pagination={false}
        style={error ? { marginTop: 12 } : undefined}
        locale={{
          emptyText: (
            <EmptyState
              title="No expenses yet"
              description="Add what you spent from the funds paid out, with a receipt or invoice for each item."
              action={<Button icon={<PlusOutlined />} onClick={() => openDrawer(null)} disabled={disabled}>Add expense</Button>}
            />
          ),
        }}
        columns={columns}
      />

      {loose.length > 0 && (
        <div className="panel-section loose-files">
          <span className="muted">Uploads not on any item:</span>
          {loose.map((doc) => (
            <span key={doc.id} className="loose-file">
              <DocumentLink doc={doc} />
              <Popconfirm title="Delete this file?" description={doc.original_name} okText="Delete" okButtonProps={{ danger: true }} onConfirm={() => deleteUpload(doc)}>
                <Button size="small" type="text" icon={<CloseOutlined />} aria-label={`Delete ${doc.original_name}`} disabled={disabled} />
              </Popconfirm>
            </span>
          ))}
        </div>
      )}

      {rows.length > 0 && <ExpenseTotals total={total} requested={r.amount_requested} disbursed={r.disbursed_total} />}

      {rows.length > 0 && (overspent || overage > 0 || underspend > 0) && (
        <div className="panel-section">
          {overspent && (
            <Form layout="vertical" requiredMark={false} style={{ maxWidth: 720 }}>
              <Form.Item
                label="Why the spend is above the amount requested"
                validateStatus={justificationError ? 'error' : undefined}
                help={justificationError ?? undefined}
                style={{ marginBottom: 24 }} // room for the character count under the textarea
              >
                <Input.TextArea
                  rows={2}
                  maxLength={1000}
                  showCount
                  value={justification}
                  disabled={disabled}
                  placeholder={`The expenses are ${money(total - requested)} above the ${money(requested)} requested. Explain why.`}
                  onChange={(e) => {
                    setJustification(e.target.value)
                    setJustificationError(null)
                  }}
                />
              </Form.Item>
            </Form>
          )}
          {overage > 0 ? (
            <div className="cell-sub">
              {money(overage)} above the {money(disbursed)} paid out will be reserved on {r.project.code}'s budget when you submit, and paid to you as a top-up once Finance verifies the expenses.
              {toNumber(r.overage_reserved) > 0 && ` ${money(r.overage_reserved)} is reserved from your last submission.`}
            </div>
          ) : underspend > 0 ? (
            <div className="cell-sub">
              {money(underspend)} of the {money(disbursed)} paid out is unspent. You return it to Finance when the request is liquidated.
            </div>
          ) : null}
        </div>
      )}

      <div className="panel-foot">
        <span className={dirty ? 'unsaved' : 'subtle'}>{dirty ? 'Unsaved changes' : locked.length ? `${plural(locked.length, 'approved item')} locked` : 'All changes saved'}</span>
        <div className="action-buttons">
          <Button onClick={save} disabled={disabled || !dirty} loading={busy === 'save'}>
            Save draft
          </Button>
          {(r.allowed_actions.includes('submit_expenses') || rows.length > 0) && (
            <Button type="primary" onClick={submit} disabled={disabled || !rows.length} loading={busy === 'submit'}>
              Submit expenses
            </Button>
          )}
        </div>
      </div>

      <ItemDrawer
        requestId={r.id}
        draft={editing}
        open={drawerOpen}
        loose={loose}
        onUploaded={(doc) => setUploads((u) => [...u, doc])}
        onClose={() => setDrawerOpen(false)}
        onSave={(d) => {
          setDrafts((ds) => (ds.some((x) => x.key === d.key) ? ds.map((x) => (x.key === d.key ? d : x)) : [...ds, d]))
          setDrawerOpen(false)
        }}
      />
    </Panel>
  )
}

// ---------- Add / edit one item ----------

interface ItemValues {
  description: string
  amount: string
  expense_date: Dayjs
  category?: string
  comment?: string
}

interface DrawerProps {
  requestId: number
  /** The item being edited, or null for a new one. */
  draft: Draft | null
  open: boolean
  /** Uploads not on any item, offered for attaching. */
  loose: DocumentOut[]
  onUploaded: (doc: DocumentOut) => void
  onSave: (draft: Draft) => void
  onClose: () => void
}

function ItemDrawer({ requestId, draft, open, loose, onUploaded, onSave, onClose }: DrawerProps) {
  const [form] = Form.useForm<ItemValues>()
  const [docs, setDocs] = useState<DocumentOut[]>([])
  const [uploading, setUploading] = useState(0)
  const [uploadError, setUploadError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    form.resetFields()
    setUploadError(null)
    setDocs(draft?.documents ?? [])
    if (draft) {
      form.setFieldsValue({
        description: draft.description,
        amount: draft.amount,
        expense_date: dayjs(draft.expense_date),
        category: draft.category ?? undefined,
        comment: draft.comment ?? '',
      })
    }
  }, [open, draft, form])

  const attachable = loose.filter((doc) => !docs.some((d) => d.id === doc.id))

  const finish = (v: ItemValues) =>
    onSave({
      key: draft?.key ?? `new-${crypto.randomUUID()}`,
      id: draft?.id,
      description: v.description.trim(),
      amount: String(v.amount),
      expense_date: v.expense_date.format('YYYY-MM-DD'),
      category: v.category ?? null,
      comment: v.comment?.trim() ? v.comment.trim() : null,
      documents: docs,
    })

  return (
    <Drawer
      open={open}
      onClose={onClose}
      size={440}
      title={draft ? 'Edit expense' : 'Add expense'}
      forceRender // keep the Form mounted so values can be set before the drawer opens
      footer={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
          <span className="cell-sub">Saved with the draft</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <Button onClick={onClose}>Cancel</Button>
            <Button type="primary" onClick={() => form.submit()} disabled={uploading > 0}>
              {draft ? 'Update item' : 'Add item'}
            </Button>
          </div>
        </div>
      }
    >
      <Form<ItemValues> form={form} layout="vertical" requiredMark={false} onFinish={finish}>
        <Form.Item
          name="description"
          label="Description"
          rules={[
            { required: true, whitespace: true, message: 'Describe the expense' },
            { max: 500, message: 'Keep it under 500 characters' },
          ]}
        >
          <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} maxLength={500} placeholder="e.g. Bus tickets to the field site" />
        </Form.Item>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)', gap: 12 }}>
          <Form.Item
            name="amount"
            label="Amount"
            rules={[
              { required: true, message: 'Enter the amount' },
              {
                validator: (_: unknown, v: string | undefined) =>
                  v === undefined || v === null || v === '' || (toNumber(v) > 0 && AMOUNT_PATTERN.test(String(v)))
                    ? Promise.resolve()
                    : Promise.reject(new Error(toNumber(v) <= 0 ? 'Must be more than zero' : 'Use at most two decimals')),
              },
            ]}
          >
            <InputNumber<string>
              stringMode
              min="0"
              prefix="₹"
              style={{ width: '100%' }}
              formatter={(v) => groupAmountInput(v)}
              parser={(v) => parseAmountInput(v)}
            />
          </Form.Item>
          <Form.Item name="expense_date" label="Date" rules={[{ required: true, message: 'Choose the date' }]}>
            <DatePicker style={{ width: '100%' }} format="DD MMM YYYY" disabledDate={(d) => d.isAfter(dayjs(), 'day')} allowClear={false} />
          </Form.Item>
        </div>
        <Form.Item name="category" label={<>Category <span className="subtle" style={{ marginLeft: 4 }}>(optional)</span></>}>
          <Select allowClear placeholder="Choose a category" options={EXPENSE_CATEGORIES.map((c) => ({ value: c, label: c }))} />
        </Form.Item>
        <Form.Item
          name="comment"
          label={<>Comment <span className="subtle" style={{ marginLeft: 4 }}>(optional)</span></>}
          rules={[{ max: 1000, message: 'Keep it under 1,000 characters' }]}
        >
          <Input.TextArea rows={2} maxLength={1000} placeholder="Anything the approver should know" />
        </Form.Item>

        <Form.Item
          label="Proof"
          validateStatus={uploadError ? 'error' : undefined}
          help={uploadError ?? undefined}
          extra={uploadError ? undefined : 'Receipt or invoice: PDF, JPG or PNG. At least one is needed before you submit.'}
          style={{ marginBottom: 0 }}
        >
          {docs.length > 0 && (
            <ul className="drawer-files">
              {docs.map((doc) => (
                <li key={doc.id}>
                  <DocumentLink doc={doc} />
                  <span className="subtle num">{fileSize(doc.size_bytes)}</span>
                  <Button
                    size="small"
                    type="text"
                    icon={<CloseOutlined />}
                    aria-label={`Remove ${doc.original_name}`}
                    onClick={() => setDocs((ds) => ds.filter((d) => d.id !== doc.id))}
                  />
                </li>
              ))}
            </ul>
          )}
          <Upload
            accept={ACCEPT}
            multiple
            showUploadList={false}
            customRequest={async ({ file, onSuccess, onError }) => {
              setUploading((n) => n + 1)
              setUploadError(null)
              try {
                const doc = await expensesApi.upload(requestId, file as File)
                setDocs((ds) => [...ds, doc])
                onUploaded(doc)
                onSuccess?.(doc)
              } catch (err) {
                const e = toApiError(err)
                setUploadError(`${(file as File).name}: ${e.message}`)
                onError?.(err as Error)
              } finally {
                setUploading((n) => n - 1)
              }
            }}
          >
            <Button icon={<UploadOutlined />} loading={uploading > 0}>
              {uploading > 0 ? 'Uploading…' : docs.length ? 'Upload another file' : 'Upload file'}
            </Button>
          </Upload>
          {attachable.length > 0 && (
            <div className="attach-earlier">
              <div className="cell-sub">Earlier uploads not on any item</div>
              {attachable.map((doc) => (
                <div key={doc.id} className="attach-row">
                  <DocumentLink doc={doc} />
                  <Button size="small" type="link" onClick={() => setDocs((ds) => [...ds, doc])}>
                    Attach
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Form.Item>
      </Form>
    </Drawer>
  )
}
