import { useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { refreshAccessToken, session, setSessionExpiredHandler } from '../api/client'
import { authApi } from '../api/endpoints'
import type { Me, TokenResponse } from '../api/types'

type Status = 'loading' | 'authenticated' | 'anonymous'

interface AuthValue {
  status: Status
  user: Me | null
  can: (permission: string) => boolean
  login: (email: string, password: string) => Promise<void>
  acceptInvite: (token: string, fullName: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<Status>('loading')
  const [user, setUser] = useState<Me | null>(null)

  const endSession = useCallback(() => {
    session.clear()
    queryClient.clear()
    setUser(null)
    setStatus('anonymous')
  }, [queryClient])

  // Restore the session after a reload: refresh token -> access token -> /me.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const restored = await refreshAccessToken()
      if (cancelled) return // React dev mode runs effects twice; only the live run continues
      if (!restored) {
        setStatus('anonymous')
        return
      }
      try {
        const me = await authApi.me()
        if (!cancelled) {
          setUser(me)
          setStatus('authenticated')
        }
      } catch {
        if (!cancelled) endSession()
      }
    })()
    return () => {
      cancelled = true
    }
  }, [endSession])

  useEffect(() => {
    setSessionExpiredHandler(endSession)
    return () => setSessionExpiredHandler(null)
  }, [endSession])

  const begin = useCallback((res: TokenResponse) => {
    session.start(res.access_token, res.refresh_token)
    setUser(res.user)
    setStatus('authenticated')
  }, [])

  const value = useMemo<AuthValue>(
    () => ({
      status,
      user,
      can: (permission) => user?.permissions.includes(permission) ?? false,
      login: async (email, password) => begin(await authApi.login(email, password)),
      acceptInvite: async (token, fullName, password) => begin(await authApi.acceptInvite(token, fullName, password)),
      logout: endSession,
    }),
    [status, user, begin, endSession],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

/** The signed-in user; only call below <RequireAuth>. */
export function useCurrentUser(): Me {
  const { user } = useAuth()
  if (!user) throw new Error('useCurrentUser called without a signed-in user')
  return user
}
