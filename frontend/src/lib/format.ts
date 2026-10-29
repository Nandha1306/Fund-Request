// Display formatting. Money stays a decimal string end to end; it is only parsed for display and charts.

const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2 })
const day = new Intl.DateTimeFormat('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
const dayTime = new Intl.DateTimeFormat('en-IN', {
  day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
})
const time = new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false })

export const toNumber = (v: string | number | null | undefined) => (v === null || v === undefined ? 0 : Number(v))

export function money(value: string | number | null | undefined): string {
  return inr.format(toNumber(value))
}

/** Signed money for ledger rows: "+₹1,000.00" / "−₹1,000.00". */
export function signedMoney(value: string | number): string {
  const n = toNumber(value)
  return `${n < 0 ? '−' : '+'}${inr.format(Math.abs(n))}`
}

export const formatDate = (iso: string) => day.format(new Date(iso))
export const formatDateTime = (iso: string) => dayTime.format(new Date(iso))

/** "Today 14:05", "Yesterday 09:12", or "28 Oct 2026". */
export function shortWhen(iso: string): string {
  const d = new Date(iso)
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (d.toDateString() === today.toDateString()) return `Today ${time.format(d)}`
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${time.format(d)}`
  return day.format(d)
}

/** "in 3 days", "in 5 hours", "expired". */
export function timeUntil(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now()
  if (ms <= 0) return 'expired'
  const hours = Math.round(ms / 3_600_000)
  if (hours < 1) return 'in under an hour'
  if (hours < 48) return `in ${hours} hour${hours === 1 ? '' : 's'}`
  return `in ${Math.round(hours / 24)} days`
}

export function percent(part: string | number, whole: string | number): number {
  const w = toNumber(whole)
  return w > 0 ? Math.round((toNumber(part) / w) * 100) : 0
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]!.toUpperCase())
    .join('')
}

/** Indian digit grouping while typing an amount ("500000.5" -> "5,00,000.5"). */
export function groupAmountInput(value: string | number | undefined): string {
  if (value === undefined || value === null || value === '') return ''
  const [int, dec] = String(value).split('.')
  const grouped = int ? Number(int).toLocaleString('en-IN') : '0'
  return dec !== undefined ? `${grouped}.${dec}` : grouped
}

export const parseAmountInput = (value: string | undefined) => (value ?? '').replace(/[^\d.]/g, '')
