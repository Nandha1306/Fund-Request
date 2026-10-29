import { Spin } from 'antd'
import type { ReactNode } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { NoAccess } from '../components/States'
import { useAuth } from './AuthProvider'

export function FullScreenLoader() {
  return (
    <div className="center-screen">
      <Spin />
    </div>
  )
}

/** Signed-in area: waits for the session to be restored, otherwise sends the user to /login. */
export function RequireAuth() {
  const { status } = useAuth()
  const location = useLocation()
  if (status === 'loading') return <FullScreenLoader />
  if (status === 'anonymous') return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  return <Outlet />
}

/** Page-level permission gate. The backend enforces the same rule; this only avoids a broken screen. */
export function RequirePermission({ permission, children }: { permission: string; children: ReactNode }) {
  const { can } = useAuth()
  return can(permission) ? <>{children}</> : <NoAccess />
}
