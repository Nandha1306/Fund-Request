import type { RequestStatus } from '../api/types'
import { money, toNumber } from '../lib/format'

const STAGES = ['Request', 'Approval', 'Disbursement', 'Expenses', 'Expense review', 'Liquidation'] as const

type Tone = 'normal' | 'returned' | 'closed'

// Which stage a status sits in. LIQUIDATED is past the last stage, so every stage reads as done.
const POSITION: Record<RequestStatus, { stage: number; tone: Tone; note?: string }> = {
  RETURNED_FOR_CORRECTION: { stage: 0, tone: 'returned', note: 'Returned for correction' },
  WITHDRAWN: { stage: 0, tone: 'closed', note: 'Withdrawn' },
  PENDING_APPROVAL: { stage: 1, tone: 'normal' },
  PENDING_DISBURSEMENT: { stage: 2, tone: 'normal' },
  PENDING_EXPENSE_SUBMISSION: { stage: 3, tone: 'normal' },
  EXPENSE_RETURNED: { stage: 3, tone: 'returned', note: 'Expenses returned' },
  PENDING_EXPENSE_APPROVAL: { stage: 4, tone: 'normal' },
  PENDING_VERIFICATION: { stage: 5, tone: 'normal' },
  LIQUIDATED: { stage: STAGES.length, tone: 'normal' },
}

/** The six workflow stages in one strip: done, current (or returned) and upcoming. */
export function StageTracker({ status, disbursedTotal }: { status: RequestStatus; disbursedTotal?: string }) {
  const pos = POSITION[status]
  const paid = toNumber(disbursedTotal) > 0

  return (
    <ol className="stages" aria-label="Request progress">
      {STAGES.map((label, i) => {
        let state: string
        let note: string
        if (i < pos.stage) {
          state = 'done'
          note = i === 2 && paid ? `${money(disbursedTotal)} paid` : 'Done'
        } else if (i === pos.stage) {
          state = pos.tone === 'normal' ? 'current' : pos.tone
          note = pos.note ?? 'Current step'
        } else {
          state = pos.tone === 'closed' ? 'closed' : 'upcoming'
          // A request returned after payout keeps its money (only a shortfall is paid later).
          note = i === 2 && paid ? `${money(disbursedTotal)} paid earlier` : pos.tone === 'closed' ? '—' : 'Upcoming'
        }
        return (
          <li key={label} className={`stage ${state}`} aria-current={i === pos.stage ? 'step' : undefined}>
            <span className="stage-bar" />
            <span className="stage-label">
              <span className="stage-num">{i + 1}</span>
              {label}
            </span>
            <span className="stage-note">{note}</span>
          </li>
        )
      })}
    </ol>
  )
}
