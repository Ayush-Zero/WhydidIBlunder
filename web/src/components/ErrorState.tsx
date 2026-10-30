import type { ReactNode } from 'react'

interface ErrorStateProps {
  title?: string
  message: string
  action?: ReactNode
}

export function ErrorState({
  title = 'Something went wrong',
  message,
  action,
}: ErrorStateProps) {
  return (
    <section className="state-card state-card--error" role="alert">
      <p className="eyebrow">Error</p>
      <h2>{title}</h2>
      <p className="state-copy">{message}</p>
      {action ? <div className="state-actions">{action}</div> : null}
    </section>
  )
}
