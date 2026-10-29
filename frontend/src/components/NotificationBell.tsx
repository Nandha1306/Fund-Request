import { BellOutlined } from '@ant-design/icons'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge, Button, Popover, Skeleton, Tooltip } from 'antd'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { notificationsApi } from '../api/endpoints'
import type { Notification } from '../api/types'
import { shortWhen } from '../lib/format'
import { EmptyState, ErrorState } from './States'

const LATEST = 15

/** Bell in the sidebar account area: unread count (polled), latest notifications, open the request on click. */
export function NotificationBell() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)

  const unread = useQuery({
    queryKey: ['notifications', 'unread-count'],
    queryFn: notificationsApi.unreadCount,
    refetchInterval: 30_000,
  })
  const list = useQuery({
    queryKey: ['notifications', 'list'],
    queryFn: () => notificationsApi.list({ size: LATEST }),
    enabled: open,
    staleTime: 0, // always fresh when the dropdown opens
  })
  const count = unread.data?.count ?? 0
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['notifications'] })

  const markRead = useMutation({ mutationFn: notificationsApi.markRead, onSettled: refresh })
  const markAll = useMutation({ mutationFn: notificationsApi.markAllRead, onSettled: refresh })

  const openNotification = (n: Notification) => {
    setOpen(false)
    if (!n.read_at) markRead.mutate(n.id)
    if (n.request_id) navigate(`/requests/${n.request_id}`)
  }

  const items = list.data?.items ?? []
  const content = (
    <div className="notif-list">
      <div className="notif-head">
        <span className="panel-title">Notifications</span>
        <Button type="link" size="small" style={{ paddingInline: 0 }} disabled={count === 0} loading={markAll.isPending} onClick={() => markAll.mutate()}>
          Mark all read
        </Button>
      </div>
      {list.isError ? (
        <ErrorState error={list.error} onRetry={() => list.refetch()} />
      ) : list.isPending ? (
        <Skeleton active paragraph={{ rows: 3 }} style={{ padding: 14 }} />
      ) : items.length === 0 ? (
        <EmptyState title="You're all caught up" description="Updates on your requests will appear here." />
      ) : (
        <ul className="notif-items">
          {items.map((n) => (
            <li
              key={n.id}
              className={n.read_at ? undefined : 'unread'}
              role="button"
              tabIndex={0}
              onClick={() => openNotification(n)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  openNotification(n)
                }
              }}
            >
              <span className="notif-dot" />
              <div style={{ minWidth: 0 }}>
                <div className="notif-text">{n.message}</div>
                <div className="cell-sub">{shortWhen(n.created_at)}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )

  return (
    <Popover
      open={open}
      onOpenChange={setOpen}
      trigger="click"
      placement="rightBottom"
      arrow={false}
      content={content}
      styles={{ container: { padding: 0 } }}
    >
      <span>
        <Tooltip title={open ? undefined : count ? `${count} unread` : 'Notifications'} placement="right">
          {/* Sits just above the bell, so a two-digit count covers neither the bell nor the sign-out icon. */}
          <Badge count={count} size="small" offset={[-8, -2]} overflowCount={99}>
            <Button type="text" size="small" icon={<BellOutlined style={{ color: '#8f9aa8' }} />} aria-label={count ? `Notifications, ${count} unread` : 'Notifications'} />
          </Badge>
        </Tooltip>
      </span>
    </Popover>
  )
}
