import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { jsonResponse } from './test/http'

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchMock.mockReset()
  fetchMock.mockResolvedValue(
    jsonResponse({ status: 'ok', database: 'ok', schema_version: 1 }),
  )
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('routed shell', () => {
  it('renders the home page and a ready server', async () => {
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
    expect(await screen.findByText('Server ready')).toBeInTheDocument()
  })

  it('renders the not-found page for an unknown route', async () => {
    render(
      <MemoryRouter initialEntries={['/not-a-route']}>
        <App />
      </MemoryRouter>,
    )

    expect(
      screen.getByRole('heading', { name: /not on the board/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /return home/i })).toBeInTheDocument()
    expect(await screen.findByText('Server ready')).toBeInTheDocument()
  })

  it('shows a friendly offline state when the API is stopped', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Server offline')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('shows a friendly not-ready state when /health is 503', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { status: 'unavailable', database: 'error' },
        { status: 503 },
      ),
    )

    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByText('Server not ready')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })
})
