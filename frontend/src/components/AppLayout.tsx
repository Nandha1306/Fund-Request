import {
  AppstoreOutlined,
  AuditOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  LogoutOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Button, Tooltip } from 'antd'
import { Suspense, type ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { useAuth, useCurrentUser } from '../auth/AuthProvider'
import { FullScreenLoader } from '../auth/guards'
import { initials } from '../lib/format'
import { ROLE_LABEL } from '../lib/labels'
import { NotificationBell } from './NotificationBell'

interface NavEntry {
  to: string
  label: string
  icon: ReactNode
  permission?: string
}

const WORKSPACE: NavEntry[] = [
  { to: '/', label: 'Dashboard', icon: <AppstoreOutlined /> },
  { to: '/requests', label: 'Requests', icon: <FileTextOutlined /> },
  { to: '/projects', label: 'Projects', icon: <FolderOpenOutlined /> },
]

const ADMIN: NavEntry[] = [
  { to: '/users', label: 'Users & access', icon: <TeamOutlined />, permission: 'user.manage' },
  { to: '/audit', label: 'Audit log', icon: <AuditOutlined />, permission: 'audit.view' },
]

export function BrandMark() {
  return <span className="brand-mark">FD</span>
}

function NavGroup({ title, items }: { title: string; items: NavEntry[] }) {
  if (items.length === 0) return null
  return (
    <>
      <div className="nav-group">{title}</div>
      {items.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
          {item.icon}
          {item.label}
        </NavLink>
      ))}
    </>
  )
}

export function AppLayout() {
  const { can, logout } = useAuth()
  const user = useCurrentUser()
  const allowed = (items: NavEntry[]) => items.filter((i) => !i.permission || can(i.permission))

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <BrandMark />
          <div>
            <div className="brand-name">Fund Desk</div>
            <div className="brand-sub">Requests &amp; liquidation</div>
          </div>
        </div>
        <nav className="nav">
          <NavGroup title="Workspace" items={allowed(WORKSPACE)} />
          <NavGroup title="Administration" items={allowed(ADMIN)} />
        </nav>
        <div className="account">
          <span className="avatar">{initials(user.full_name)}</span>
          <div className="account-text">
            <div className="account-name">{user.full_name}</div>
            <div className="account-role">{ROLE_LABEL[user.role]}</div>
          </div>
          <div className="account-tools">
            <NotificationBell />
            <Tooltip title="Sign out" placement="right">
              <Button type="text" size="small" icon={<LogoutOutlined style={{ color: '#8f9aa8' }} />} onClick={logout} aria-label="Sign out" />
            </Tooltip>
          </div>
        </div>
      </aside>
      <main className="main">
        <Suspense fallback={<FullScreenLoader />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  )
}
