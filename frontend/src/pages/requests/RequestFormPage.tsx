import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, App, Button, Form, Input, InputNumber, Select, Skeleton } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { projectsApi, requestsApi, usersApi } from '../../api/endpoints'
import { fieldErrors, toApiError } from '../../api/errors'
import type { HistoryEntry, Project, RequestDetail } from '../../api/types'
import { CodePill } from '../../components/Badges'
import { BudgetBar } from '../../components/Budget'
import { PageHeader } from '../../components/PageHeader'
import { Panel } from '../../components/Panel'
import { EmptyState, ErrorState, NotFound } from '../../components/States'
import { formatDateTime, groupAmountInput, money, parseAmountInput, toNumber } from '../../lib/format'
import { afterRequestChange } from './cache'

interface Values {
  project_id: number
  approver_id: number
  amount_requested: string
  purpose: string
  supporting_info?: string
}

const AMOUNT_PATTERN = /^\d+(\.\d{1,2})?$/

/** The most recent reject / return, shown to the requestor while correcting the request. */
function lastReturn(history: HistoryEntry[] | undefined): HistoryEntry | undefined {
  return [...(history ?? [])].reverse().find((h) => h.to_status === 'RETURNED_FOR_CORRECTION' && h.reason)
}

function ProjectBudget({ project, disbursed }: { project?: Project; disbursed: number }) {
  if (!project) return <EmptyState title="Choose a project" description="Its budget appears here so you can see how much is available." />
  return (
    <div className="panel-body">
      <dl className="kv" style={{ gridTemplateColumns: '120px 1fr', marginBottom: 14 }}>
        <dt>Project</dt>
        <dd>
          {project.name} <CodePill>{project.code}</CodePill>
        </dd>
        <dt>Allocated</dt>
        <dd className="num">{money(project.allocated)}</dd>
        <dt>Available</dt>
        <dd className="num" style={{ fontWeight: 600 }}>{money(project.available)}</dd>
        {disbursed > 0 && (
          <>
            <dt>Paid earlier</dt>
            <dd className="num">{money(disbursed)}</dd>
          </>
        )}
      </dl>
      <BudgetBar project={project} />
      <div className="cell-sub" style={{ marginTop: 10 }}>
        {disbursed > 0
          ? `${money(disbursed)} was already paid out on this request, so only the amount above it is reserved and paid later.`
          : 'The amount is reserved on this budget as soon as you submit, and released if the request is rejected or withdrawn.'}
      </div>
    </div>
  )
}

export default function RequestFormPage() {
  const params = useParams()
  const resubmitId = params.id !== undefined ? Number(params.id) : null
  const resubmitting = resubmitId !== null
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { message } = App.useApp()
  const [form] = Form.useForm<Values>()
  const [formError, setFormError] = useState<string | null>(null)
  const [stale, setStale] = useState(false)
  // The version the form was filled from; resubmit sends exactly this one.
  const loadedVersion = useRef<number | null>(null)
  const loadedFrom = useRef<string | null>(null) // "id:version" the fields were filled from

  const request = useQuery({
    queryKey: ['request', resubmitId],
    queryFn: () => requestsApi.get(resubmitId!),
    enabled: resubmitting && Number.isFinite(resubmitId),
  })
  const history = useQuery({
    queryKey: ['request', resubmitId, 'history'],
    queryFn: () => requestsApi.history(resubmitId!),
    enabled: resubmitting && Number.isFinite(resubmitId),
  })
  const projects = useQuery({ queryKey: ['projects', { status: 'ACTIVE', size: 100 }], queryFn: () => projectsApi.list({ status: 'ACTIVE', size: 100 }) })
  const approvers = useQuery({ queryKey: ['approvers'], queryFn: usersApi.approvers })

  const detail = request.data
  const projectId = Form.useWatch('project_id', form)
  const project = projects.data?.items.find((p) => p.id === (detail?.project.id ?? projectId))
  const disbursed = toNumber(detail?.disbursed_total)

  // Create: preselect ?project=ID. Resubmit: fill from the request once it and the approver list are loaded.
  useEffect(() => {
    if (resubmitting || !projects.data) return
    const wanted = Number(searchParams.get('project'))
    if (wanted && projects.data.items.some((p) => p.id === wanted) && form.getFieldValue('project_id') === undefined) {
      form.setFieldsValue({ project_id: wanted })
    }
  }, [resubmitting, projects.data, searchParams, form])

  useEffect(() => {
    if (!detail || !approvers.data || loadedFrom.current === `${detail.id}:${detail.version}` || !detail.allowed_actions.includes('resubmit')) return
    loadedFrom.current = `${detail.id}:${detail.version}`
    loadedVersion.current = detail.version
    form.setFieldsValue({
      project_id: detail.project.id,
      approver_id: approvers.data.some((a) => a.id === detail.approver.id) ? detail.approver.id : undefined,
      amount_requested: detail.amount_requested,
      purpose: detail.purpose,
      supporting_info: detail.supporting_info ?? '',
    })
  }, [detail, approvers.data, form])

  const save = useMutation({
    mutationFn: (v: Values): Promise<RequestDetail> => {
      const body = {
        approver_id: v.approver_id,
        amount_requested: String(v.amount_requested),
        purpose: v.purpose.trim(),
        supporting_info: v.supporting_info?.trim() ? v.supporting_info.trim() : null,
      }
      if (resubmitting) return requestsApi.resubmit(resubmitId!, { version: loadedVersion.current!, ...body })
      return requestsApi.create({ project_id: v.project_id, ...body })
    },
    onSuccess: (saved) => {
      afterRequestChange(queryClient, saved)
      message.success(resubmitting ? `${saved.ref_no} resubmitted for approval` : `${saved.ref_no} submitted for approval`)
      navigate(`/requests/${saved.id}`)
    },
    onError: (err) => {
      const e = toApiError(err)
      const fields = fieldErrors(e)
      const available = (e.details as { available?: string } | null)?.available
      if (Object.keys(fields).length) {
        form.setFields(Object.entries(fields).map(([name, msg]) => ({ name: name as keyof Values, errors: [msg] })))
      } else if (e.status === 422 && available !== undefined) {
        // Replace the "more than available" warning with the server's error, so the field says it once.
        form.setFields([{ name: 'amount_requested', errors: [`Only ${money(available)} is available on this project`], warnings: [] }])
      } else if (e.status === 409 && resubmitting) {
        setStale(true)
      } else {
        setFormError(e.message)
      }
    },
  })

  const reload = async () => {
    setStale(false)
    setFormError(null)
    await request.refetch()
    history.refetch()
  }

  if (resubmitting) {
    if (request.isError) {
      return toApiError(request.error).status === 404 ? <NotFound what="request" /> : <div className="page"><ErrorState error={request.error} onRetry={() => request.refetch()} /></div>
    }
    if (!detail) return <div className="page"><Skeleton active paragraph={{ rows: 8 }} /></div>
  }

  const crumbs = detail
    ? [{ label: 'Requests', to: '/requests' }, { label: detail.ref_no, to: `/requests/${detail.id}` }, { label: 'Resubmit' }]
    : [{ label: 'Requests', to: '/requests' }, { label: 'New request' }]

  if (detail && !detail.allowed_actions.includes('resubmit')) {
    return (
      <div className="page">
        <PageHeader crumbs={crumbs} title={`Resubmit ${detail.ref_no}`} />
        <Panel>
          <EmptyState
            title="This request can't be resubmitted"
            description="Only the requestor can resubmit, and only while the request is returned for correction."
            action={<Link to={`/requests/${detail.id}`}><Button>Back to the request</Button></Link>}
          />
        </Panel>
      </div>
    )
  }

  const returned = lastReturn(history.data)
  const amountRules = [
    { required: true, message: 'Enter the amount' },
    {
      validator: (_: unknown, v: string | undefined) =>
        v === undefined || v === null || v === '' || (toNumber(v) > 0 && AMOUNT_PATTERN.test(String(v)))
          ? Promise.resolve()
          : Promise.reject(new Error(toNumber(v) <= 0 ? 'Must be more than zero' : 'Use at most two decimals')),
    },
    {
      warningOnly: true,
      validator: (_: unknown, v: string | undefined) =>
        project && toNumber(v) - disbursed > toNumber(project.available)
          ? Promise.reject(new Error(`More than the ${money(project.available)} available on ${project.code}. It will be refused unless the budget is increased.`))
          : Promise.resolve(),
    },
  ]

  return (
    <div className="page">
      <PageHeader
        crumbs={crumbs}
        title={detail ? `Resubmit ${detail.ref_no}` : 'New fund request'}
        description={
          detail
            ? 'Correct the request and send it back for approval. The approver is notified.'
            : 'Ask for funds against one of your projects. The approver you choose is notified.'
        }
      />
      <div className="detail-grid" style={{ marginTop: 0 }}>
        <Panel title="Request details">
          {stale && (
            <Alert
              type="warning"
              showIcon
              style={{ marginBottom: 16 }}
              title="This request changed"
              description="Someone else updated it while you were editing. Reload to see the latest version; your edits will be replaced."
              action={<Button size="small" onClick={reload} loading={request.isFetching}>Reload</Button>}
            />
          )}
          {formError && <Alert type="error" showIcon title={formError} style={{ marginBottom: 16 }} />}
          <Form<Values>
            form={form}
            layout="vertical"
            requiredMark={false}
            onFinish={(v) => {
              setFormError(null)
              save.mutate(v)
            }}
          >
            <Form.Item
              name="project_id"
              label="Project"
              rules={[{ required: true, message: 'Choose a project' }]}
              extra={detail ? 'The project of a request cannot be changed.' : undefined}
            >
              <Select<number>
                placeholder="Choose a project"
                disabled={resubmitting}
                loading={projects.isPending}
                showSearch={{ optionFilterProp: 'label' }}
                notFoundContent={projects.isPending ? undefined : 'No active projects you are a member of'}
                options={
                  detail && !project
                    ? [{ value: detail.project.id, label: `${detail.project.code} · ${detail.project.name}`, available: null }]
                    : (projects.data?.items ?? []).map((p) => ({ value: p.id, label: `${p.code} · ${p.name}`, available: p.available }))
                }
                optionRender={(o) => (
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                    <span>{o.data.label}</span>
                    {o.data.available !== null && <span className="muted num">{money(o.data.available)} available</span>}
                  </div>
                )}
              />
            </Form.Item>
            {detail && !project && projects.data && (
              <Alert
                type="warning"
                showIcon
                style={{ marginTop: -6, marginBottom: 16 }}
                title={`${detail.project.code} is not open for requests`}
                description="The project is inactive or you are no longer a member, so resubmitting will be refused. You can still withdraw the request."
              />
            )}
            <Form.Item name="approver_id" label="Approver" rules={[{ required: true, message: 'Choose an approver' }]} extra="The approver is notified and decides on this request.">
              <Select<number>
                placeholder="Choose an approver"
                loading={approvers.isPending}
                showSearch={{ optionFilterProp: 'label' }}
                options={(approvers.data ?? []).map((u) => ({ value: u.id, label: u.full_name, email: u.email }))}
                optionRender={(o) => (
                  <div>
                    <div>{o.data.label}</div>
                    <div className="cell-sub">{o.data.email}</div>
                  </div>
                )}
              />
            </Form.Item>
            <Form.Item name="amount_requested" label="Amount" rules={amountRules} dependencies={['project_id']} style={{ maxWidth: 320 }}>
              <InputNumber<string>
                stringMode
                min="0"
                prefix="₹"
                style={{ width: '100%' }}
                formatter={(v) => groupAmountInput(v)}
                parser={(v) => parseAmountInput(v)}
              />
            </Form.Item>
            <Form.Item
              name="purpose"
              label="Purpose"
              rules={[
                { required: true, whitespace: true, message: 'Describe what the funds are for' },
                { max: 2000, message: 'Keep it under 2,000 characters' },
              ]}
            >
              <Input.TextArea rows={3} showCount maxLength={2000} placeholder="What the funds are for, and when they are needed" />
            </Form.Item>
            <Form.Item
              name="supporting_info"
              label={<>Supporting information <span className="subtle" style={{ marginLeft: 4 }}>(optional)</span></>}
              rules={[{ max: 4000, message: 'Keep it under 4,000 characters' }]}
            >
              <Input.TextArea rows={3} showCount maxLength={4000} placeholder="Quotes, breakdown or anything that helps the approver decide" />
            </Form.Item>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <Button onClick={() => navigate(detail ? `/requests/${detail.id}` : '/requests')}>Cancel</Button>
              <Button type="primary" htmlType="submit" loading={save.isPending} disabled={stale}>
                {detail ? 'Resubmit for approval' : 'Submit for approval'}
              </Button>
            </div>
          </Form>
        </Panel>
        <div>
          {returned && (
            <Panel title="Why it was returned" subtitle={`${returned.actor_name ?? 'System'} · ${formatDateTime(returned.created_at)}`}>
              <div className="prewrap">{returned.reason}</div>
            </Panel>
          )}
          <Panel title="Project budget" flush>
            <ProjectBudget project={project} disbursed={disbursed} />
          </Panel>
        </div>
      </div>
    </div>
  )
}
