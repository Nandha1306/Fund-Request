import { Tooltip } from 'antd'

import type { Project } from '../api/types'
import { money, percent, toNumber } from '../lib/format'

type Buckets = Pick<Project, 'allocated' | 'reserved' | 'outstanding' | 'liquidated' | 'available'>

const SEGMENTS = [
  { key: 'liquidated', label: 'Liquidated', color: 'var(--budget-liquidated)' },
  { key: 'outstanding', label: 'Disbursed', color: 'var(--budget-outstanding)' },
  { key: 'reserved', label: 'Reserved', color: 'var(--budget-reserved)' },
] as const

/** Stacked bar: liquidated, disbursed (not yet settled), reserved; the grey track is what is still available. */
export function BudgetBar({ project, thin }: { project: Buckets; thin?: boolean }) {
  const total = toNumber(project.allocated)
  const tip = (
    <div style={{ fontSize: 12 }}>
      {SEGMENTS.map((s) => (
        <div key={s.key}>
          {s.label}: {money(project[s.key])}
        </div>
      ))}
      <div>Available: {money(project.available)}</div>
    </div>
  )
  return (
    <Tooltip title={tip}>
      <div className={`budget-bar${thin ? ' thin' : ''}`} aria-label="Budget usage">
        {total > 0 &&
          SEGMENTS.map((s) => {
            const width = (toNumber(project[s.key]) / total) * 100
            return width > 0 ? <span key={s.key} style={{ width: `${width}%`, background: s.color }} /> : null
          })}
      </div>
    </Tooltip>
  )
}

export function BudgetLegend() {
  return (
    <div className="legend">
      {SEGMENTS.map((s) => (
        <span key={s.key}>
          <i className="swatch" style={{ background: s.color }} />
          {s.label}
        </span>
      ))}
      <span>
        <i className="swatch" style={{ background: 'var(--budget-available)' }} />
        Available
      </span>
    </div>
  )
}

/** Share of the allocation already committed (reserved + disbursed + liquidated). */
export function committedPercent(p: Buckets): number {
  return percent(toNumber(p.allocated) - toNumber(p.available), p.allocated)
}

/** The five budget figures in one strip, with the bar underneath. */
export function BudgetFigures({ project }: { project: Buckets }) {
  const figures = [
    { label: 'Allocated', value: project.allocated, note: 'Total budget' },
    { label: 'Reserved', value: project.reserved, color: 'var(--budget-reserved)', note: 'Held by open requests' },
    { label: 'Disbursed', value: project.outstanding, color: 'var(--budget-outstanding)', note: 'Paid out, not yet settled' },
    { label: 'Liquidated', value: project.liquidated, color: 'var(--budget-liquidated)', note: 'Verified final spend' },
    { label: 'Available', value: project.available, note: `${100 - committedPercent(project)}% of allocation` },
  ]
  return (
    <>
      <div className="figures">
        {figures.map((f) => (
          <div className="figure" key={f.label}>
            <div className="figure-label">
              {f.color && <i className="swatch" style={{ background: f.color }} />}
              {f.label}
            </div>
            <div className="figure-value">{money(f.value)}</div>
            <div className="figure-note">{f.note}</div>
          </div>
        ))}
      </div>
      <div style={{ padding: '0 16px 14px' }}>
        <BudgetBar project={project} />
      </div>
    </>
  )
}
