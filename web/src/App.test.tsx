import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useNavigate } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import gamesFixture from './api/fixtures/games.json'
import positionsFixture from './api/fixtures/positions.json'
import reportFixture from './api/fixtures/report.json'
import { reportPath } from './lib/analysis'
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

  it('reloads a saved report from its durable URL', async () => {
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
      if (url.includes('/report?')) {
        return Promise.resolve(jsonResponse(reportFixture))
      }
      if (url.includes('/positions?')) {
        return Promise.resolve(jsonResponse(positionsFixture))
      }
      return Promise.reject(new Error(`unexpected request: ${url}`))
    })

    render(
      <MemoryRouter
        initialEntries={[
          reportPath(
            'beast-ash',
            'chesscom:7515eb15-befd-11f1-8dbb-feb0c301000f',
            'white',
          ),
        ]}
      >
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByTestId('review-board')).toHaveAttribute(
      'data-fen',
      positionsFixture.starting_fen,
    )
  })

  it('moves between discovery and a saved report with browser history', async () => {
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
      if (url.includes('/report?')) {
        return Promise.resolve(jsonResponse(reportFixture))
      }
      if (url.includes('/positions?')) {
        return Promise.resolve(jsonResponse(positionsFixture))
      }
      return Promise.reject(new Error(`unexpected request: ${url}`))
    })

    const user = userEvent.setup()
    render(
      <MemoryRouter
        initialEntries={[
          '/player/beast-ash',
          reportPath(
            'beast-ash',
            'chesscom:7515eb15-befd-11f1-8dbb-feb0c301000f',
            'white',
          ),
        ]}
        initialIndex={1}
      >
        <HistoryControls />
        <App />
      </MemoryRouter>,
    )

    expect(await screen.findByTestId('review-board')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'History back' }))
    expect(
      await screen.findByRole('heading', { name: /games for beast-ash/i }),
    ).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'History forward' }))
    expect(await screen.findByTestId('review-board')).toBeInTheDocument()
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

function HistoryControls() {
  const navigate = useNavigate()
  return (
    <div>
      <button type="button" onClick={() => navigate(-1)}>
        History back
      </button>
      <button type="button" onClick={() => navigate(1)}>
        History forward
      </button>
    </div>
  )
}
