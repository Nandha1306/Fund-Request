import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { DatePicker, Select, Table } from 'antd'
import type { Dayjs } from 'dayjs'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { auditApi, type AuditQuery } from '../api/endpoints'
import type { AuditEntry } from '../api/types'
import { PageHeader } from '../components/PageHeader'
import { Panel } from '../components/Panel'
import { EmptyState, ErrorState } from '../components/States'
import { actionLabel, entityLabel, fieldChanges, memberChanges, snapshotFields, subjectLink, summarize } from '../lib/audit'
import { formatDateTime } from '../lib/format'
import { ACTION_LABEL, ENTITY_LABEL } from '../lib/labels'

const PAGE_SIZE = 25

function Details({ entry }: { entry: AuditEntry }) {
  const changes = fieldChanges(entry)
  const fields = snapshotFields(entry)
  const members = memberChanges(entry)
  return (
    <div style={{ padding: '4px 0 4px 8px' }}>
      {changes.length > 0 && (
        <table className="diff">
          <tbody>
            {changes.map((c) => (
              <tr key={c.field}>
                <th>{c.label}</th>
                <td><span className="old">{c.before}</span></td>
                <td>{c.after}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {fields.length > 0 && (
        <table className="diff">
          <tbody>
            {fields.map((f) => (
              <tr key={f.label}>
                <th>{f.label}</th>
                <td>{f.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {entry.action === 'members_changed' && (
        <table className="diff">
          <tbody>
            <tr>
              <th>Added</th>
              <td>{members.added.join(', ') || '—'}</td>
            </tr>
            <tr>
              <th>Removed</th>
              <td>{members.removed.join(', ') || '—'}</td>
            </tr>
          </tbody>
        </table>
      )}
      {entry.reason && <div style={{ marginTop: 6 }}>Reason: {entry.reason}</div>}
      {!changes.length && !fields.length && entry.action !== 'members_changed' && !entry.reason && <span className="subtle">No further details recorded.</span>}
    </div>
  )
}

export default function AuditPage() {
  const [entity, setEntity] = useState<string | undefined>()
  const [action, setAction] = useState<string | undefined>()
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const [page, setPage] = useState(1)

  const query: AuditQuery = {
    page,
    size: PAGE_SIZE,
    entity,
    action,
    date_from: range?.[0]?.format('YYYY-MM-DD'),
    date_to: range?.[1]?.format('YYYY-MM-DD'),
  }
  const audit = useQuery({ queryKey: ['audit', query], queryFn: () => auditApi.list(query), placeholderData: keepPreviousData })
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v)
    setPage(1)
  }
  const filtered = Boolean(entity || action || range)

  return (
    <div className="page">
      <PageHeader title="Audit log" description="Every change to requests, projects, users and invitations: who made it, when, and what changed." />
      <Panel flush>
        <div className="toolbar">
          <Select allowClear placeholder="All records" style={{ width: 160 }} value={entity} onChange={reset(setEntity)} options={Object.entries(ENTITY_LABEL).map(([value, label]) => ({ value, label }))} />
          <Select allowClear placeholder="All actions" style={{ width: 180 }} value={action} onChange={reset(setAction)} options={Object.entries(ACTION_LABEL).map(([value, label]) => ({ value, label }))} />
          <DatePicker.RangePicker value={range} onChange={(v) => reset(setRange)(v)} allowEmpty={[true, true]} format="DD MMM YYYY" />
        </div>
        {audit.isError ? (
          <ErrorState error={audit.error} onRetry={() => audit.refetch()} />
        ) : (
          <Table<AuditEntry>
            size="small"
            rowKey="id"
            loading={audit.isFetching && !audit.data}
            dataSource={audit.data?.items}
            expandable={{ expandedRowRender: (e) => <Details entry={e} />, expandRowByClick: true }}
            locale={{ emptyText: <EmptyState title={filtered ? 'Nothing matches these filters' : 'No activity recorded yet'} /> }}
            pagination={{ current: page, pageSize: PAGE_SIZE, total: audit.data?.total, onChange: setPage, hideOnSinglePage: true, showSizeChanger: false, showTotal: (t) => `${t} entr${t === 1 ? 'y' : 'ies'}` }}
            columns={[
              { title: 'When', width: 160, render: (_, e) => <span className="muted num">{formatDateTime(e.created_at)}</span> },
              { title: 'By', width: 150, render: (_, e) => e.actor_name ?? <span className="subtle">System</span> },
              { title: 'Action', width: 140, render: (_, e) => actionLabel(e) },
              {
                title: 'Record',
                width: 380,
                render: (_, e) => {
                  const label = e.subject_label ?? `${entityLabel(e)} #${e.entity_id}`
                  const link = subjectLink(e)
                  return (
                    <div>
                      <span className="subtle">{entityLabel(e)} · </span>
                      {link ? <Link to={link} onClick={(ev) => ev.stopPropagation()}>{label}</Link> : label}
                    </div>
                  )
                },
              },
              { title: 'Change', render: (_, e) => <span className="muted">{summarize(e)}</span> },
            ]}
          />
        )}
      </Panel>
    </div>
  )
}
