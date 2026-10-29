import { SearchOutlined } from '@ant-design/icons'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Button, Input, Select, Tabs } from 'antd'
import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { projectsApi, requestsApi, type RequestQuery } from '../../api/endpoints'
import type { RequestStatus } from '../../api/types'
import { useAuth } from '../../auth/AuthProvider'
import { PageHeader } from '../../components/PageHeader'
import { Panel } from '../../components/Panel'
import { EmptyState, ErrorState } from '../../components/States'
import { REQUEST_STATUS_OPTIONS } from '../../lib/labels'
import { RequestTable } from './RequestTable'

const PAGE_SIZE = 20

function RequestList({ waiting }: { waiting: boolean }) {
  const { can } = useAuth()
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<RequestStatus[]>([])
  const [projectId, setProjectId] = useState<number | undefined>()
  const [page, setPage] = useState(1)

  const projects = useQuery({ queryKey: ['projects', { size: 100 }], queryFn: () => projectsApi.list({ size: 100 }) })
  const query: RequestQuery = {
    page,
    size: PAGE_SIZE,
    q: search.trim() || undefined,
    status: status.length ? status : undefined,
    project_id: projectId,
    mine: waiting ? 'action' : undefined,
  }
  const requests = useQuery({ queryKey: ['requests', query], queryFn: () => requestsApi.list(query), placeholderData: keepPreviousData })
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v)
    setPage(1)
  }
  const filtered = Boolean(search.trim() || status.length || projectId)
  const canCreate = can('request.create')

  const empty = filtered ? (
    <EmptyState title="No requests match these filters" />
  ) : waiting ? (
    <EmptyState title="Nothing is waiting on you" description="Requests that need your approval, payment or correction will appear here." />
  ) : (
    <EmptyState
      title="No requests yet"
      description={canCreate ? 'Raise a fund request against one of your projects.' : 'Requests you can see will appear here.'}
      action={canCreate && <Button onClick={() => navigate('/requests/new')}>New request</Button>}
    />
  )

  return (
    <>
      <div className="toolbar">
        <Input
          allowClear
          prefix={<SearchOutlined className="subtle" />}
          placeholder="Search ref or purpose"
          style={{ width: 240 }}
          value={search}
          onChange={(e) => reset(setSearch)(e.target.value)}
        />
        <Select<RequestStatus[]>
          mode="multiple"
          allowClear
          showSearch={{ optionFilterProp: 'label' }}
          placeholder="Any status"
          style={{ minWidth: 200, maxWidth: 360 }}
          maxTagCount="responsive"
          value={status}
          onChange={reset(setStatus)}
          options={REQUEST_STATUS_OPTIONS}
        />
        <Select<number>
          allowClear
          showSearch={{ optionFilterProp: 'label' }}
          placeholder="All projects"
          style={{ width: 220 }}
          loading={projects.isPending}
          value={projectId}
          onChange={reset(setProjectId)}
          options={(projects.data?.items ?? []).map((p) => ({ value: p.id, label: `${p.code} · ${p.name}` }))}
        />
      </div>
      {requests.isError ? (
        <ErrorState error={requests.error} onRetry={() => requests.refetch()} />
      ) : (
        <RequestTable
          requests={requests.data?.items}
          loading={requests.isFetching && !requests.data}
          emptyText={empty}
          pagination={{
            current: page,
            pageSize: PAGE_SIZE,
            total: requests.data?.total,
            onChange: setPage,
            showSizeChanger: false,
            hideOnSinglePage: true,
            showTotal: (t) => `${t} request${t === 1 ? '' : 's'}`,
          }}
        />
      )}
    </>
  )
}

export default function RequestsPage() {
  const { can } = useAuth()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = searchParams.get('tab') === 'all' ? 'all' : 'waiting'
  // A one-row query just for the tab count; shares the ['requests'] key so it refreshes after every action.
  const waitingCount = useQuery({ queryKey: ['requests', { mine: 'action', size: 1 }], queryFn: () => requestsApi.list({ mine: 'action', size: 1 }) })
  const count = waitingCount.data?.total ?? 0

  return (
    <div className="page">
      <PageHeader
        title="Fund requests"
        description="Raise, approve and pay out fund requests. Each request shows whose turn it is."
        actions={can('request.create') && <Button type="primary" onClick={() => navigate('/requests/new')}>New request</Button>}
      />
      <Panel flush>
        <Tabs
          activeKey={tab}
          onChange={(k) => setSearchParams(k === 'all' ? { tab: 'all' } : {}, { replace: true })}
          items={[
            { key: 'waiting', label: count ? `Waiting on me (${count})` : 'Waiting on me', children: <RequestList waiting /> },
            { key: 'all', label: 'All requests', children: <RequestList waiting={false} /> },
          ]}
        />
      </Panel>
    </div>
  )
}
