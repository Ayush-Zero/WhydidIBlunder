import { useId, type ReactNode } from 'react'

interface EmptyStateProps {
  title: string
  description?: ReactNode
  action?: ReactNode
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  const titleId = useId()

  return (
    <section className="state-card" aria-labelledby={titleId}>
      <p className="eyebrow">Nothing here yet</p>
      <h2 id={titleId}>{title}</h2>
      {description ? <div className="state-copy">{description}</div> : null}
      {action ? <div className="state-actions">{action}</div> : null}
    </section>
  )
}
