import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'

// Same-origin '/api': proxied by Vite in dev and by nginx in Docker, so no CORS in either.
export const api = axios.create({ baseURL: '/api' })

/**
 * Session tokens. The access token lives in memory only; the refresh token is kept in
 * localStorage so a page reload can restore the session without asking for the password.
 */
const REFRESH_KEY = 'funddesk.refresh'
let accessToken: string | null = null

export const session = {
  start(access: string, refresh: string) {
    accessToken = access
    localStorage.setItem(REFRESH_KEY, refresh)
  },
  clear() {
    accessToken = null
    localStorage.removeItem(REFRESH_KEY)
  },
  hasRefresh: () => localStorage.getItem(REFRESH_KEY) !== null,
}

let onExpired: (() => void) | null = null
/** Called when the session can no longer be refreshed (logged out elsewhere, user deactivated…). */
export function setSessionExpiredHandler(handler: (() => void) | null) {
  onExpired = handler
}

let refreshing: Promise<string | null> | null = null

/** Exchange the stored refresh token for a new access token. Concurrent callers share one call. */
export function refreshAccessToken(): Promise<string | null> {
  const refresh = localStorage.getItem(REFRESH_KEY)
  if (!refresh) return Promise.resolve(null)
  refreshing ??= axios
    .post<{ access_token: string }>('/api/auth/refresh', { refresh_token: refresh })
    .then((res) => (accessToken = res.data.access_token))
    .catch(() => {
      session.clear()
      return null
    })
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

api.interceptors.request.use((config) => {
  if (accessToken) config.headers.Authorization = `Bearer ${accessToken}`
  return config
})

type RetryableConfig = InternalAxiosRequestConfig & { _retried?: boolean }

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const config = error.config as RetryableConfig | undefined
    const isAuthCall = config?.url?.startsWith('/auth/')
    if (error.response?.status === 401 && config && !config._retried && !isAuthCall) {
      config._retried = true
      if (await refreshAccessToken()) return api(config)
      onExpired?.()
    }
    return Promise.reject(error)
  },
)
