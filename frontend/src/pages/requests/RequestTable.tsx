import { Table, type TableColumnsType, type TablePaginationConfig } from 'antd'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

import type { RequestSummary } from '../../api/types'
import { CodePill, RequestStatusBadge } from '../../components/Badges'
import { formatDate, money } from '../../lib/format'

interface Props {
  requests?: RequestSummary[]
  loading?: boolean
  emptyText: ReactNode
  pagination?: false | TablePaginationConfig
  /** Leave out the project column (on a project's own page). */
  hideProject?: boolean
}

/** The request list used on the Requests page, the dashboard and a project's Requests tab. */
export function RequestTable({ requests, loading, emptyText, pagination = false, hideProject }: Props) {
  const navigate = useNavigate()
  const columns: TableColumnsType<RequestSummary> = [
    { title: 'Ref', width: 140, render: (_, r) => <CodePill>{r.ref_no}</CodePill> },
    ...(hideProject
      ? []
      : [
          {
            title: 'Project',
            render: (_: unknown, r: RequestSummary) => (
              <div>
                <div className="cell-main">{r.project.name}</div>
                <div className="cell-sub">{r.project.code}</div>
              </div>
            ),
          },
        ]),
    { title: 'Requestor', width: 170, render: (_, r) => r.requestor.full_name },
    { title: 'Approver', width: 170, render: (_, r) => r.approver.full_name },
    {
      title: 'Amount',
      align: 'right',
      width: 150,
      render: (_, r) => <span className="num">{money(r.amount_approved ?? r.amount_requested)}</span>,
    },
    { title: 'Status', width: 180, render: (_, r) => <RequestStatusBadge status={r.status} /> },
    { title: 'Updated', width: 120, render: (_, r) => <span className="muted">{formatDate(r.updated_at)}</span> },
  ]

  return (
    <Table<RequestSummary>
      size="small"
      rowKey="id"
      loading={loading}
      dataSource={requests}
      rowClassName={() => 'row-link'}
      onRow={(r) => ({ onClick: () => navigate(`/requests/${r.id}`) })}
      locale={{ emptyText }}
      pagination={pagination}
      columns={columns}
    />
  )
}
