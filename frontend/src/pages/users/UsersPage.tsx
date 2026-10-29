import { SearchOutlined } from '@ant-design/icons'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Button, Input, Select, Table, Tabs, Tag, Tooltip } from 'antd'
import { useState } from 'react'

import { usersApi, type UserQuery } from '../../api/endpoints'
import type { AdminUser, Invitation, Role } from '../../api/types'
import { useCurrentUser } from '../../auth/AuthProvider'
import { RoleBadge, UserStatusBadge } from '../../components/Badges'
import { PageHeader } from '../../components/PageHeader'
import { Panel } from '../../components/Panel'
import { EmptyState, ErrorState } from '../../components/States'
import { formatDate, initials, timeUntil } from '../../lib/format'
import { ROLE_OPTIONS } from '../../lib/labels'
import { EditUserDrawer } from './EditUserDrawer'
import { InviteModal } from './InviteModal'

const PAGE_SIZE = 20

function PeopleTab({ onEdit }: { onEdit: (u: AdminUser) => void }) {
  const me = useCurrentUser()
  const [search, setSearch] = useState('')
  const [role, setRole] = useState<Role | undefined>()
  const [active, setActive] = useState<'active' | 'inactive' | undefined>()
  const [page, setPage] = useState(1)

  const query: UserQuery = { page, size: PAGE_SIZE, q: search.trim() || undefined, role, is_active: active === undefined ? undefined : active === 'active' }
  const users = useQuery({ queryKey: ['users', query], queryFn: () => usersApi.list(query), placeholderData: keepPreviousData })
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v)
    setPage(1)
  }

  return (
    <>
      <div className="toolbar">
        <Input allowClear prefix={<SearchOutlined className="subtle" />} placeholder="Search name or email" style={{ width: 240 }} value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
        <Select allowClear placeholder="All roles" style={{ width: 150 }} options={ROLE_OPTIONS} value={role} onChange={reset(setRole)} />
        <Select
          allowClear
          placeholder="Any status"
          style={{ width: 140 }}
          value={active}
          onChange={reset(setActive)}
          options={[
            { value: 'active', label: 'Active' },
            { value: 'inactive', label: 'Deactivated' },
          ]}
        />
      </div>
      {users.isError ? (
        <ErrorState error={users.error} onRetry={() => users.refetch()} />
      ) : (
        <Table<AdminUser>
          size="small"
          rowKey="id"
          loading={users.isFetching && !users.data}
          dataSource={users.data?.items}
          locale={{ emptyText: <EmptyState title="No users match these filters" /> }}
          pagination={{ current: page, pageSize: PAGE_SIZE, total: users.data?.total, onChange: setPage, hideOnSinglePage: true, showSizeChanger: false, showTotal: (t) => `${t} user${t === 1 ? '' : 's'}` }}
          columns={[
            {
              title: 'Name',
              render: (_, u) => (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span className="avatar" style={{ background: '#e8edf3', color: '#3a4655' }}>{initials(u.full_name)}</span>
                  <div>
                    <div className="cell-main">
                      {u.full_name}
                      {u.id === me.id && <span className="subtle" style={{ fontWeight: 400 }}> (you)</span>}
                    </div>
                    <div className="cell-sub">{u.email}</div>
                  </div>
                </div>
              ),
            },
            {
              title: 'Role',
              width: 210,
              render: (_, u) => (
                <span style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                  <RoleBadge role={u.role} />
                  {u.role === 'SUPER_ADMIN' && u.can_finance_ops && (
                    <Tooltip title="Can disburse, verify and liquidate">
                      <Tag variant="outlined" style={{ marginInlineEnd: 0 }}>+ finance ops</Tag>
                    </Tooltip>
                  )}
                </span>
              ),
            },
            { title: 'Status', width: 130, render: (_, u) => <UserStatusBadge active={u.is_active} /> },
            { title: 'Joined', width: 130, render: (_, u) => <span className="muted">{formatDate(u.created_at)}</span> },
            { title: '', width: 80, align: 'right', render: (_, u) => <Button size="small" onClick={() => onEdit(u)}>Edit</Button> },
          ]}
        />
      )}
    </>
  )
}

function InvitationsTab({ invitations, loading, error, onRetry }: { invitations?: Invitation[]; loading: boolean; error: unknown; onRetry: () => void }) {
  if (error) return <ErrorState error={error} onRetry={onRetry} />
  return (
    <Table<Invitation>
      size="small"
      rowKey="id"
      loading={loading}
      dataSource={invitations}
      pagination={false}
      locale={{ emptyText: <EmptyState title="No pending invitations" description="Invitations disappear from this list once accepted or expired." /> }}
      columns={[
        { title: 'Email', render: (_, i) => <span className="cell-main">{i.email}</span> },
        { title: 'Role', width: 160, render: (_, i) => <RoleBadge role={i.role} /> },
        { title: 'Sent', width: 140, render: (_, i) => <span className="muted">{formatDate(i.created_at)}</span> },
        { title: 'Expires', width: 160, render: (_, i) => <span className="muted">{timeUntil(i.expires_at)}</span> },
      ]}
    />
  )
}

export default function UsersPage() {
  const [inviting, setInviting] = useState(false)
  const [editing, setEditing] = useState<AdminUser | null>(null)
  const invitations = useQuery({ queryKey: ['invitations'], queryFn: usersApi.invitations })
  const pending = invitations.data?.length ?? 0

  return (
    <div className="page">
      <PageHeader
        title="Users & access"
        description="Invite people, set their role and switch access on or off. Every change is recorded in the audit log."
        actions={<Button type="primary" onClick={() => setInviting(true)}>Invite user</Button>}
      />
      <Panel flush>
        <Tabs
          items={[
            { key: 'people', label: 'People', children: <PeopleTab onEdit={setEditing} /> },
            {
              key: 'invitations',
              label: pending ? `Pending invitations (${pending})` : 'Pending invitations',
              children: <InvitationsTab invitations={invitations.data} loading={invitations.isPending} error={invitations.error} onRetry={() => invitations.refetch()} />,
            },
          ]}
        />
      </Panel>
      <InviteModal open={inviting} onClose={() => setInviting(false)} />
      <EditUserDrawer user={editing} onClose={() => setEditing(null)} />
    </div>
  )
}
