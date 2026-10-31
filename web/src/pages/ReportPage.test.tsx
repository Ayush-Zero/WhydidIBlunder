import { render, screen } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
} from 'vitest'
import { testPositions, testReport } from '../test/handlers'
import { server, startMockServer, stopMockServer } from '../test/server'
import { ReportPage } from './ReportPage'

beforeAll(startMockServer)

afterEach(() => {
  server.resetHandlers()
})

afterAll(stopMockServer)

const VALID_QUERY =
  '?game_key=chesscom%3Agame-1&user_color=white'

function renderReport(search = VALID_QUERY) {
  return render(
    <MemoryRouter initialEntries={[`/player/beast-ash/report${search}`]}>
      <Routes>
        <Route path="/player/:username/report" element={<ReportPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('report page', () => {
  it('shows the loading state while the report is pending', async () => {
    let resolveReport: (response: Response) => void = () => {}
    let markStarted: () => void = () => {}
    const started = new Promise<void>((resolve) => {
      markStarted = resolve
    })
    server.use(
      http.get(
        '*/api/games/*/report',
        () =>
          new Promise<Response>((resolve) => {
            resolveReport = resolve
            markStarted()
          }),
      ),
    )

    renderReport()
    await started

    expect(
      screen.getByText(/loading the board and moves/i),
    ).toBeInTheDocument()

    resolveReport(HttpResponse.json(testReport))
    expect(await screen.findByTestId('review-board')).toBeInTheDocument()
  })

  it('loads the report from the encoded query on a direct visit', async () => {
    let requested = ''
    server.use(
      http.get('*/api/games/*/report', ({ request }) => {
        requested = request.url
        return HttpResponse.json(testReport)
      }),
    )

    renderReport()

    expect(await screen.findByTestId('review-board')).toHaveAttribute(
      'data-fen',
      testPositions.starting_fen,
    )
    expect(
      screen.getByRole('heading', {
        name: /Adolf Anderssen vs Lionel Kieseritzky/i,
      }),
    ).toBeInTheDocument()

    const url = new URL(requested)
    expect(url.pathname).toBe('/api/games/chesscom:game-1/report')
    expect(url.searchParams.get('user_color')).toBe('white')
  })

  it('shows an empty state when the report has no moves', async () => {
    server.use(
      http.get('*/api/games/*/report', () =>
        HttpResponse.json({ ...testReport, moves: [] }),
      ),
    )

    renderReport()

    expect(
      await screen.findByText(/this report has no moves to review/i),
    ).toBeInTheDocument()
    for (const link of screen.getAllByRole('link', { name: /back to games/i })) {
      expect(link).toHaveAttribute('href', '/player/beast-ash')
    }
  })

  it('explains an incomplete report link and offers a route back', async () => {
    renderReport('?game_key=chesscom%3Agame-1&user_color=purple')

    expect(
      await screen.findByRole('heading', {
        name: /this report link is incomplete/i,
      }),
    ).toBeInTheDocument()
    for (const link of screen.getAllByRole('link', { name: /back to games/i })) {
      expect(link).toHaveAttribute('href', '/player/beast-ash')
    }
  })

  it('shows a recoverable error when the report has expired', async () => {
    server.use(
      http.get('*/api/games/*/report', () =>
        HttpResponse.json({ detail: 'report not found' }, { status: 404 }),
      ),
    )

    renderReport()

    expect(
      await screen.findByRole('heading', {
        name: /this report is no longer available/i,
      }),
    ).toBeInTheDocument()
    expect(screen.getByText('report not found')).toBeInTheDocument()
    for (const link of screen.getAllByRole('link', { name: /back to games/i })) {
      expect(link).toHaveAttribute('href', '/player/beast-ash')
    }
  })
})
