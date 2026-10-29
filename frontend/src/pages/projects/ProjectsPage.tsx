import { SearchOutlined } from '@ant-design/icons'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Button, Input, Segmented, Table } from 'antd'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { projectsApi } from '../../api/endpoints'
import type { Project, ProjectStatus } from '../../api/types'
import { useAuth } from '../../auth/AuthProvider'
import { CodePill, ProjectStatusBadge } from '../../components/Badges'
import { BudgetBar, BudgetLegend, committedPercent } from '../../components/Budget'
import { PageHeader } from '../../components/PageHeader'
import { Panel } from '../../components/Panel'
import { EmptyState, ErrorState } from '../../components/States'
import { formatDate, money } from '../../lib/format'
import { ProjectFormModal } from './ProjectFormModal'

const PAGE_SIZE = 20
type StatusFilter = 'ALL' | ProjectStatus

export default function ProjectsPage() {
  const { can } = useAuth()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<StatusFilter>('ALL')
  const [page, setPage] = useState(1)
  const creating = searchParams.get('new') === '1'
  const manager = can('project.manage')

  const query = { page, size: PAGE_SIZE, q: search.trim() || undefined, status: status === 'ALL' ? undefined : status }
  const projects = useQuery({ queryKey: ['projects', query], queryFn: () => projectsApi.list(query), placeholderData: keepPreviousData })

  const setCreating = (open: boolean) => setSearchParams(open ? { new: '1' } : {}, { replace: true })
  const total = projects.data?.total ?? 0

  return (
    <div className="page">
      <PageHeader
        title="Projects"
        description={manager ? 'Budgets, members and the money trail of every project.' : 'Projects you have access to.'}
        actions={manager && <Button type="primary" onClick={() => setCreating(true)}>New project</Button>}
      />
      <Panel flush>
        <div className="toolbar">
          <Input
            allowClear
            prefix={<SearchOutlined className="subtle" />}
            placeholder="Search by code or name"
            style={{ width: 260 }}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
          />
          {manager && (
            <Segmented<StatusFilter>
              value={status}
              onChange={(v) => {
                setStatus(v)
                setPage(1)
              }}
              options={[
                { value: 'ALL', label: 'All' },
                { value: 'ACTIVE', label: 'Active' },
                { value: 'INACTIVE', label: 'Inactive' },
              ]}
            />
          )}
          <span className="grow" />
          <BudgetLegend />
        </div>
        {projects.isError ? (
          <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
        ) : (
          <Table<Project>
            size="small"
            rowKey="id"
            loading={projects.isFetching && !projects.data}
            dataSource={projects.data?.items}
            rowClassName={() => 'row-link'}
            onRow={(p) => ({ onClick: () => navigate(`/projects/${p.id}`) })}
            locale={{
              emptyText:
                search || status !== 'ALL' ? (
                  <EmptyState title="No projects match these filters" />
                ) : (
                  <EmptyState
                    title="No projects yet"
                    description={manager ? 'Create the first project to start allocating budget.' : 'You have not been added to a project yet.'}
                    action={manager && <Button onClick={() => setCreating(true)}>New project</Button>}
                  />
                ),
            }}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              total,
              onChange: setPage,
              showSizeChanger: false,
              hideOnSinglePage: true,
              showTotal: (t) => `${t} project${t === 1 ? '' : 's'}`,
            }}
            columns={[
              { title: 'Code', width: 120, render: (_, p) => <CodePill>{p.code}</CodePill> },
              {
                title: 'Project',
                render: (_, p) => (
                  <div>
                    <div className="cell-main">{p.name}</div>
                    {p.description && <div className="cell-sub" style={{ maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.description}</div>}
                  </div>
                ),
              },
              { title: 'Status', width: 100, render: (_, p) => <ProjectStatusBadge status={p.status} /> },
              { title: 'Allocated', align: 'right', width: 150, render: (_, p) => <span className="num">{money(p.allocated)}</span> },
              {
                title: 'Usage',
                width: 170,
                render: (_, p) => (
                  <div>
                    <BudgetBar project={p} thin />
                    <div className="cell-sub">{committedPercent(p)}% committed</div>
                  </div>
                ),
              },
              { title: 'Available', align: 'right', width: 150, render: (_, p) => <span className="num">{money(p.available)}</span> },
              { title: 'Updated', width: 110, render: (_, p) => <span className="muted">{formatDate(p.updated_at)}</span> },
            ]}
          />
        )}
      </Panel>
      {manager && <ProjectFormModal open={creating} onClose={() => setCreating(false)} onSaved={(p) => navigate(`/projects/${p.id}`)} />}
    </div>
  )
}
