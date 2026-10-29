import { Skeleton } from 'antd'
import { Link } from 'react-router-dom'

import type { AuditEntry } from '../api/types'
import { actionLabel, entityLabel, subjectLink, summarize } from '../lib/audit'
import { shortWhen } from '../lib/format'
import { EmptyState } from './States'

/** Compact "who did what" feed used on the dashboard and on project pages. */
export function ActivityList({ entries, loading, showSubject = true }: { entries?: AuditEntry[]; loading?: boolean; showSubject?: boolean }) {
  if (loading) return <Skeleton active paragraph={{ rows: 4 }} style={{ padding: 16 }} />
  if (!entries?.length) return <EmptyState title="No activity yet" description="Changes to requests, projects and users will appear here." />
  return (
    <ul className="activity">
      {entries.map((e) => {
        const summary = summarize(e)
        const subject = e.subject_label ?? `${entityLabel(e)} #${e.entity_id}`
        const link = subjectLink(e)
        return (
          <li key={e.id}>
            <span className="activity-time">{shortWhen(e.created_at)}</span>
            <div className="activity-text">
              <div>
                <strong style={{ fontWeight: 500 }}>{e.actor_name ?? 'System'}</strong>{' '}
                <span className="muted">{actionLabel(e).toLowerCase()}</span>{' '}
                {showSubject && (link ? <Link to={link}>{subject}</Link> : subject)}
              </div>
              {summary && <div className="cell-sub">{summary}</div>}
            </div>
          </li>
        )
      })}
    </ul>
  )
}
