import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('routed shell', () => {
  it('renders the home page', () => {
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    )

    expect(
      screen.getByRole('heading', {
        name: /find the move that changed the game/i,
      }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('link', { name: /whydidiblunder home/i }),
    ).toBeInTheDocument()
  })

  it('renders the not-found page for an unknown route', () => {
    render(
      <MemoryRouter initialEntries={['/not-a-route']}>
        <App />
      </MemoryRouter>,
    )

    expect(
      screen.getByRole('heading', { name: /not on the board/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /return home/i })).toBeInTheDocument()
  })
})
