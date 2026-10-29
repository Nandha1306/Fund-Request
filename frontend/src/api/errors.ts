import { isAxiosError } from 'axios'

/** The backend's error envelope: {"error": {"code", "message", "details"}}. */
export interface ApiError {
  status: number | null
  code: string
  message: string
  details: unknown
}

interface ValidationIssue {
  loc?: (string | number)[]
  msg?: string
}

const cleanMessage = (msg: string) => msg.replace(/^Value error, /, '')

function isIssueList(details: unknown): details is ValidationIssue[] {
  return Array.isArray(details) && details.every((d) => typeof d === 'object' && d !== null && 'msg' in d)
}

export function toApiError(err: unknown): ApiError {
  if (isAxiosError(err)) {
    if (!err.response) {
      return { status: null, code: 'network', message: 'Cannot reach the server. Check your connection and try again.', details: null }
    }
    const body = err.response.data as { error?: { code?: string; message?: string; details?: unknown } } | undefined
    const e = body?.error
    let message = e?.message ?? 'Something went wrong. Please try again.'
    if (e?.code === 'validation_error' && isIssueList(e.details) && e.details[0]?.msg) {
      message = cleanMessage(e.details[0].msg)
    }
    return { status: err.response.status, code: e?.code ?? 'unknown', message, details: e?.details ?? null }
  }
  return { status: null, code: 'unknown', message: err instanceof Error ? err.message : 'Something went wrong.', details: null }
}

/** Map a 400 validation error onto form fields: {fieldName: message}. */
export function fieldErrors(err: ApiError): Record<string, string> {
  if (err.code !== 'validation_error' || !isIssueList(err.details)) return {}
  const out: Record<string, string> = {}
  for (const issue of err.details) {
    const field = issue.loc?.[issue.loc[0] === 'body' ? 1 : 0]
    if (typeof field === 'string' && issue.msg && !out[field]) out[field] = cleanMessage(issue.msg)
  }
  return out
}

/** Request refs listed by a 422 guard (for example a role change blocked by open requests). */
export function requestRefs(err: ApiError): string[] {
  const d = err.details as { request_refs?: unknown } | null
  return Array.isArray(d?.request_refs) ? (d.request_refs as string[]) : []
}
