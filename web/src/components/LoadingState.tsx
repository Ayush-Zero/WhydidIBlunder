interface LoadingStateProps {
  label?: string
}

export function LoadingState({ label = 'Loading…' }: LoadingStateProps) {
  return (
    <div className="state-card state-card--loading" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p>{label}</p>
    </div>
  )
}
