import axios from 'axios'

// Same-origin '/api': proxied by Vite in dev and by nginx in Docker, so no CORS in either.
// Auth (Bearer token, refresh on 401) and Idempotency-Key headers are added here in later phases.
export const api = axios.create({ baseURL: '/api' })
