import type { CSSProperties, ReactNode } from 'react'

interface Props {
  title?: ReactNode
  subtitle?: ReactNode
  extra?: ReactNode
  /** Content sits flush to the edges (tables, lists, tabs). */
  flush?: boolean
  className?: string
  style?: CSSProperties
  children: ReactNode
}

/** Bordered white section used for every block of content. */
export function Panel({ title, subtitle, extra, flush, className, style, children }: Props) {
  return (
    <section className={['panel', className].filter(Boolean).join(' ')} style={style}>
      {(title || extra) && (
        <div className="panel-head">
          <div>
            {title && <div className="panel-title">{title}</div>}
            {subtitle && <div className="panel-sub">{subtitle}</div>}
          </div>
          {extra}
        </div>
      )}
      {flush ? children : <div className="panel-body">{children}</div>}
    </section>
  )
}
