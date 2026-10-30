import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ErrorBoundary } from './ErrorBoundary'

function Broken(): never {
  throw new Error('render failed')
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ErrorBoundary', () => {
  it('keeps a render failure from becoming a blank page', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <ErrorBoundary>
        <Broken />
      </ErrorBoundary>,
    )

    expect(screen.getByRole('alert')).toHaveTextContent(
      /we could not draw that screen/i,
    )
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /reload page/i }),
    ).toBeInTheDocument()
  })
})
