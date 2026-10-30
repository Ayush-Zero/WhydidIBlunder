import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EmptyState } from './EmptyState'
import { ErrorState } from './ErrorState'
import { LoadingState } from './LoadingState'

describe('state components', () => {
  it('announces loading without taking over the page', () => {
    render(<LoadingState label="Loading games…" />)

    expect(screen.getByRole('status')).toHaveTextContent('Loading games…')
  })

  it('renders an empty state with an optional action', () => {
    render(
      <EmptyState
        title="No games found"
        description="Try another player."
        action={<button type="button">Change player</button>}
      />,
    )

    expect(
      screen.getByRole('heading', { name: /no games found/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /change player/i })).toBeInTheDocument()
  })

  it('renders an API error with a retry action', () => {
    render(
      <ErrorState
        title="Could not load games"
        message="Can't reach the analysis server."
        action={<button type="button">Retry</button>}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent(
      /can't reach the analysis server/i,
    )
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
