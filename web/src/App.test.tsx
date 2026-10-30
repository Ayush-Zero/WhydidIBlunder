import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import gamesFixture from './api/fixtures/games.json'
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
    expect(screen.getByLabelText(/chess\.com username/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /find games/i }),
    ).toBeInTheDocument()
    expect(await screen.findByText('Server ready')).toBeInTheDocument()
  })

  it('searches from the home page and shows the game list', async () => {
    fetchMock.mockImplementation((input) => {
      const url =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.href
            : input.url
      if (url === '/health') {
        return Promise.resolve(
          jsonResponse({ status: 'ok', database: 'ok', schema_version: 1 }),
        )
      }
      if (url.startsWith('/api/player/beast-ash/games')) {
        return Promise.resolve(jsonResponse(gamesFixture))
      }
      return Promise.reject(new Error(`unexpected request: ${url}`))
    })

    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <App />
      </MemoryRouter>,
    )

    await user.type(screen.getByLabelText(/chess\.com username/i), 'beast-ash')
    await user.click(screen.getByRole('button', { name: /find games/i }))

    expect(
      await screen.findAllByRole('heading', { level: 3 }),
    ).toHaveLength(2)
    expect(
      screen.getByRole('heading', { name: /games for beast-ash/i }),
    ).toBeInTheDocument()
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
