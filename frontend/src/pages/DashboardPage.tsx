import { useQuery } from '@tanstack/react-query'
import { Button, Skeleton, Table } from 'antd'
import type { CSSProperties } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { auditApi, projectsApi, requestsApi, usersApi } from '../api/endpoints'
import type { Project } from '../api/types'
import { useAuth, useCurrentUser } from '../auth/AuthProvider'
import { ActivityList } from '../components/ActivityList'
import { CodePill, ProjectStatusBadge } from '../components/Badges'
import { BudgetBar, BudgetFigures, BudgetLegend, committedPercent } from '../components/Budget'
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { EmptyState, ErrorState } from '../components/States'
import { money, toNumber } from '../lib/format'
import { RequestTable } from './requests/RequestTable'

function sumBuckets(projects: Project[]) {
  const total = (k: keyof Project) => projects.reduce((s, p) => s + toNumber(p[k] as string), 0).toFixed(2)
  return {
    allocated: total('allocated'),
    reserved: total('reserved'),
    outstanding: total('outstanding'),
    liquidated: total('liquidated'),
    available: total('available'),
  }
}

function ProjectRows({ projects, loading }: { projects: Project[]; loading: boolean }) {
  const navigate = useNavigate()
  return (
    <Table<Project>
      size="small"
      rowKey="id"
      loading={loading}
      dataSource={projects}
      pagination={false}
      rowClassName={() => 'row-link'}
      onRow={(p) => ({ onClick: () => navigate(`/projects/${p.id}`) })}
      locale={{ emptyText: <EmptyState title="No projects to show" /> }}
      columns={[
        {
          title: 'Project',
          render: (_, p) => (
            <div>
              <div className="cell-main">{p.name}</div>
              <div className="cell-sub"><CodePill>{p.code}</CodePill></div>
            </div>
          ),
        },
        { title: 'Usage', width: 150, render: (_, p) => <div><BudgetBar project={p} thin /><div className="cell-sub">{committedPercent(p)}% committed</div></div> },
        { title: 'Available', align: 'right', width: 140, render: (_, p) => <span className="num">{money(p.available)}</span> },
      ]}
    />
  )
}

const WAITING_ROWS = 6

/** Requests whose next step is the current user's, for every role (mine=action). */
function WaitingPanel() {
  const waiting = useQuery({
    queryKey: ['requests', { mine: 'action', size: WAITING_ROWS }],
    queryFn: () => requestsApi.list({ mine: 'action', size: WAITING_ROWS }),
  })
  const total = waiting.data?.total ?? 0
  return (
    <Panel
      title="Waiting on you"
      subtitle="Requests whose next step is yours"
      extra={<Link to="/requests">{total > WAITING_ROWS ? `View all ${total}` : 'All requests'}</Link>}
      flush
    >
      {waiting.isError ? (
        <ErrorState error={waiting.error} onRetry={() => waiting.refetch()} />
      ) : (
        <RequestTable
          requests={waiting.data?.items}
          loading={waiting.isPending}
          emptyText={<EmptyState title="Nothing is waiting on you" description="Requests that need your approval, payment or correction will appear here." />}
        />
      )}
    </Panel>
  )
}

function ManagerDashboard() {
  const { can } = useAuth()
  const projects = useQuery({ queryKey: ['projects', { size: 100 }], queryFn: () => projectsApi.list({ size: 100 }) })
  const activity = useQuery({ queryKey: ['audit', { size: 8 }], queryFn: () => auditApi.list({ size: 8 }), enabled: can('audit.view') })
  const activeUsers = useQuery({ queryKey: ['users', { is_active: true, size: 1 }], queryFn: () => usersApi.list({ is_active: true, size: 1 }), enabled: can('user.manage') })
  const invitations = useQuery({ queryKey: ['invitations'], queryFn: usersApi.invitations, enabled: can('user.manage') })

  const all = projects.data?.items ?? []
  const active = all.filter((p) => p.status === 'ACTIVE')
  const busiest = [...active].sort((a, b) => committedPercent(b) - committedPercent(a)).slice(0, 6)

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={projects.data ? `Budget position across ${active.length} active project${active.length === 1 ? '' : 's'}` : 'Budget position'}
        actions={can('project.manage') && <Link to="/projects?new=1"><Button type="primary">New project</Button></Link>}
      />
      <WaitingPanel />
      {projects.isError ? (
        <Panel><ErrorState error={projects.error} onRetry={() => projects.refetch()} /></Panel>
      ) : (
        <Panel title="All active projects" extra={<BudgetLegend />} flush>
          {projects.isPending ? <Skeleton active style={{ padding: 16 }} /> : <BudgetFigures project={sumBuckets(active)} />}
        </Panel>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 3fr) minmax(0, 2fr)', gap: 16, marginTop: 16 }}>
        <Panel title="Most committed projects" subtitle="Share of allocation already reserved, disbursed or liquidated" extra={<Link to="/projects">All projects</Link>} flush>
          <ProjectRows projects={busiest} loading={projects.isPending} />
        </Panel>
        <div>
          {can('user.manage') && (
            <Panel title="People" extra={<Link to="/users">Manage</Link>} flush style={{ marginBottom: 16 }}>
              <div className="figures" style={{ '--cols': 2 } as CSSProperties}>
                <div className="figure">
                  <div className="figure-label">Active users</div>
                  <div className="figure-value">{activeUsers.data?.total ?? '–'}</div>
                </div>
                <div className="figure">
                  <div className="figure-label">Pending invitations</div>
                  <div className="figure-value">{invitations.data?.length ?? '–'}</div>
                </div>
              </div>
            </Panel>
          )}
          {can('audit.view') && (
            <Panel title="Recent activity" extra={<Link to="/audit">Audit log</Link>} flush>
              <ActivityList entries={activity.data?.items} loading={activity.isPending} />
            </Panel>
          )}
        </div>
      </div>
    </>
  )
}

function MemberDashboard() {
  const user = useCurrentUser()
  const navigate = useNavigate()
  const projects = useQuery({ queryKey: ['projects', { size: 100 }], queryFn: () => projectsApi.list({ size: 100 }) })
  const isRequestor = user.role === 'REQUESTOR'
  const items = projects.data?.items ?? []

  return (
    <>
      <PageHeader
        title="Dashboard"
        description={isRequestor ? 'Projects you can raise fund requests against' : 'Projects with requests assigned to you'}
      />
      <WaitingPanel />
      <Panel
        title={isRequestor ? 'Your projects' : 'Assigned projects'}
        subtitle={isRequestor ? 'Only active projects you are a member of are listed' : undefined}
        extra={<BudgetLegend />}
        flush
      >
        {projects.isError ? (
          <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
        ) : items.length === 0 && !projects.isPending ? (
          <EmptyState
            title={isRequestor ? 'You are not on any active project yet' : 'No projects yet'}
            description={isRequestor ? 'Finance adds requestors to projects. Ask them to add you.' : 'Projects appear here once a request is assigned to you.'}
          />
        ) : (
          <Table<Project>
            size="small"
            rowKey="id"
            loading={projects.isPending}
            dataSource={items}
            pagination={false}
            rowClassName={() => 'row-link'}
            onRow={(p) => ({ onClick: () => navigate(`/projects/${p.id}`) })}
            columns={[
              { title: 'Code', width: 120, render: (_, p) => <CodePill>{p.code}</CodePill> },
              { title: 'Project', render: (_, p) => <span className="cell-main">{p.name}</span> },
              { title: 'Status', width: 110, render: (_, p) => <ProjectStatusBadge status={p.status} /> },
              { title: 'Usage', width: 180, render: (_, p) => <BudgetBar project={p} thin /> },
              { title: 'Available', align: 'right', width: 160, render: (_, p) => <span className="num">{money(p.available)}</span> },
            ]}
          />
        )}
      </Panel>
    </>
  )
}

export default function DashboardPage() {
  const { can } = useAuth()
  return <div className="page">{can('project.manage') ? <ManagerDashboard /> : <MemberDashboard />}</div>
}
