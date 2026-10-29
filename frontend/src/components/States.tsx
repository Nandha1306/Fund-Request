import { Alert, Button } from 'antd'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'

import { toApiError } from '../api/errors'
import { PageHeader } from './PageHeader'

export function EmptyState({ title, description, action }: { title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {description && <div className="empty-desc">{description}</div>}
      {action && <div style={{ marginTop: 14 }}>{action}</div>}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div style={{ padding: 16 }}>
      <Alert
        type="error"
        showIcon
        title="This could not be loaded"
        description={toApiError(error).message}
        action={onRetry && <Button size="small" onClick={onRetry}>Try again</Button>}
      />
    </div>
  )
}

export function NoAccess() {
  return (
    <div className="page">
      <PageHeader title="No access" description="Your role does not include this area. Ask a Super Admin if you need it." />
      <Link to="/">Back to the dashboard</Link>
    </div>
  )
}

export function NotFound({ what = 'page' }: { what?: string }) {
  return (
    <div className="page">
      <PageHeader title={`This ${what} does not exist`} description="It may have been removed, or you may not have access to it." />
      <Link to="/">Back to the dashboard</Link>
    </div>
  )
}
