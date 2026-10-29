import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { App, Button, Skeleton, Table, Tabs } from 'antd'
import { useState, type JSX } from 'react'
import { useParams } from 'react-router-dom'

import { auditApi, projectsApi } from '../../api/endpoints'
import { toApiError } from '../../api/errors'
import type { AuditEntry, LedgerEntry, Member, Project } from '../../api/types'
import { useAuth } from '../../auth/AuthProvider'
import { ActivityList } from '../../components/ActivityList'
import { CodePill, LedgerTypeBadge, ProjectStatusBadge } from '../../components/Badges'
import { BudgetFigures, BudgetLegend } from '../../components/Budget'
import { PageHeader } from '../../components/PageHeader'
import { Panel } from '../../components/Panel'
import { EmptyState, ErrorState, NotFound } from '../../components/States'
import { formatDate, formatDateTime, money, signedMoney } from '../../lib/format'
import { MembersModal } from './MembersModal'
import { ProjectFormModal } from './ProjectFormModal'

// The budget engine tags overspend reservations with the internal note "overage".
const ledgerNote = (note: string | null) => (note === 'overage' ? 'For overspend' : (note ?? ''))

function MembersTab({ project }: { project: Project }) {
  const [editing, setEditing] = useState(false)
  const members = useQuery({ queryKey: ['project', project.id, 'members'], queryFn: () => projectsApi.members(project.id) })
  return (
    <>
      <div className="toolbar">
        <span className="muted">Requestors who can raise fund requests against this project.</span>
        <span className="grow" />
        <Button onClick={() => setEditing(true)} disabled={!members.data}>Manage members</Button>
      </div>
      {members.isError ? (
        <ErrorState error={members.error} onRetry={() => members.refetch()} />
      ) : (
        <Table<Member>
          size="small"
          rowKey="user_id"
          loading={members.isPending}
          dataSource={members.data}
          pagination={false}
          locale={{ emptyText: <EmptyState title="No members yet" description="Add requestors so they can raise requests against this project." /> }}
          columns={[
            { title: 'Name', render: (_, m) => <span className="cell-main">{m.full_name}</span> },
            { title: 'Email', dataIndex: 'email', render: (v: string) => <span className="muted">{v}</span> },
            { title: 'Added', width: 140, render: (_, m) => <span className="muted">{formatDate(m.added_at)}</span> },
          ]}
        />
      )}
      {members.data && <MembersModal open={editing} project={project} members={members.data} onClose={() => setEditing(false)} />}
    </>
  )
}

function LedgerTab({ project }: { project: Project }) {
  const [page, setPage] = useState(1)
  const ledger = useQuery({
    queryKey: ['project', project.id, 'ledger', page],
    queryFn: () => projectsApi.ledger(project.id, page, 20),
    placeholderData: keepPreviousData,
  })
  return (
    <>
      <div className="toolbar">
        <span className="muted">Every money movement on this project, newest first. Entries are never edited or deleted.</span>
      </div>
      {ledger.isError ? (
        <ErrorState error={ledger.error} onRetry={() => ledger.refetch()} />
      ) : (
        <Table<LedgerEntry>
          size="small"
          rowKey="id"
          loading={ledger.isPending}
          dataSource={ledger.data?.items}
          pagination={{ current: page, pageSize: 20, total: ledger.data?.total, onChange: setPage, hideOnSinglePage: true, showSizeChanger: false }}
          columns={[
            { title: 'Date', width: 170, render: (_, e) => <span className="muted num">{formatDateTime(e.created_at)}</span> },
            { title: 'Entry', width: 160, render: (_, e) => <LedgerTypeBadge type={e.entry_type} /> },
            {
              title: 'Amount',
              align: 'right',
              width: 170,
              // Allocation rows store the signed change (+/-); every other row is a positive movement.
              render: (_, e) => <span className="num">{e.entry_type === 'ALLOCATE' ? signedMoney(e.amount) : money(e.amount)}</span>,
            },
            { title: 'Request', width: 140, render: (_, e) => (e.request_ref ? <CodePill>{e.request_ref}</CodePill> : <span className="subtle">—</span>) },
            { title: 'By', render: (_, e) => e.actor_name ?? <span className="subtle">System</span> },
            { title: 'Note', render: (_, e) => <span className="muted">{ledgerNote(e.note)}</span> },
          ]}
        />
      )}
    </>
  )
}

function ActivityTab({ project }: { project: Project }) {
  const activity = useQuery({
    queryKey: ['audit', { entity: 'projects', entity_id: project.id }],
    queryFn: () => auditApi.list({ entity: 'projects', entity_id: project.id, size: 50 }),
  })
  if (activity.isError) return <ErrorState error={activity.error} onRetry={() => activity.refetch()} />
  return <ActivityList entries={activity.data?.items as AuditEntry[] | undefined} loading={activity.isPending} showSubject={false} />
}

function DetailsTab({ project }: { project: Project }) {
  return (
    <div className="panel-body">
      <dl className="kv">
        <dt>Code</dt>
        <dd><CodePill>{project.code}</CodePill></dd>
        <dt>Status</dt>
        <dd><ProjectStatusBadge status={project.status} /></dd>
        <dt>Description</dt>
        <dd>{project.description || <span className="subtle">No description</span>}</dd>
        <dt>Created</dt>
        <dd>{formatDateTime(project.created_at)}</dd>
        <dt>Last updated</dt>
        <dd>{formatDateTime(project.updated_at)}</dd>
      </dl>
    </div>
  )
}

export default function ProjectDetailPage() {
  const id = Number(useParams().id)
  const { can } = useAuth()
  const { modal, message } = App.useApp()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const project = useQuery({ queryKey: ['project', id], queryFn: () => projectsApi.get(id), enabled: Number.isFinite(id) })

  const toggleStatus = useMutation({
    mutationFn: (p: Project) => projectsApi.update(p.id, { status: p.status === 'ACTIVE' ? 'INACTIVE' : 'ACTIVE' }),
    onSuccess: (p) => {
      queryClient.setQueryData(['project', p.id], p)
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      queryClient.invalidateQueries({ queryKey: ['audit'] })
      message.success(p.status === 'ACTIVE' ? 'Project activated' : 'Project deactivated')
    },
    onError: (err) => message.error(toApiError(err).message),
  })

  if (project.isError) {
    return toApiError(project.error).status === 404 ? <NotFound what="project" /> : <div className="page"><ErrorState error={project.error} onRetry={() => project.refetch()} /></div>
  }
  if (!project.data) return <div className="page"><Skeleton active paragraph={{ rows: 8 }} /></div>

  const p = project.data
  const manager = can('project.manage')

  const confirmStatus = () =>
    modal.confirm({
      title: p.status === 'ACTIVE' ? `Deactivate ${p.code}?` : `Activate ${p.code}?`,
      content:
        p.status === 'ACTIVE'
          ? 'Requestors will no longer be able to raise new requests or resubmit returned ones. Requests already past approval continue as normal, and all history stays visible.'
          : 'Members will be able to raise fund requests against this project again.',
      okText: p.status === 'ACTIVE' ? 'Deactivate' : 'Activate',
      okButtonProps: { danger: p.status === 'ACTIVE' },
      onOk: () => toggleStatus.mutateAsync(p),
    })

  const tabs = [
    can('project.members') && { key: 'members', label: 'Members', children: <MembersTab project={p} /> },
    manager && { key: 'ledger', label: 'Ledger', children: <LedgerTab project={p} /> },
    can('audit.view') && { key: 'activity', label: 'Activity', children: <ActivityTab project={p} /> },
    { key: 'details', label: 'Details', children: <DetailsTab project={p} /> },
  ].filter((t): t is { key: string; label: string; children: JSX.Element } => Boolean(t))

  return (
    <div className="page">
      <PageHeader
        crumbs={[{ label: 'Projects', to: '/projects' }, { label: p.code }]}
        title={
          <>
            {p.name}
            <ProjectStatusBadge status={p.status} />
          </>
        }
        description={<><CodePill>{p.code}</CodePill>{p.description && <span style={{ marginLeft: 8 }}>{p.description}</span>}</>}
        actions={
          manager && (
            <>
              <Button onClick={confirmStatus} loading={toggleStatus.isPending}>{p.status === 'ACTIVE' ? 'Deactivate' : 'Activate'}</Button>
              <Button type="primary" onClick={() => setEditing(true)}>Edit project</Button>
            </>
          )
        }
      />
      <Panel title="Budget" extra={<BudgetLegend />} flush>
        <BudgetFigures project={p} />
      </Panel>
      <Panel flush className="panel-gap">
        <Tabs items={tabs} />
      </Panel>
      {manager && <ProjectFormModal open={editing} project={p} onClose={() => setEditing(false)} />}
    </div>
  )
}
