import { fireEvent, render, screen, within } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
} from 'vitest'
import type { GameReport } from '../api'
import { testPositions, testReport } from '../test/handlers'
import { server, startMockServer, stopMockServer } from '../test/server'
import { Review } from './Review'

beforeAll(startMockServer)

afterEach(() => {
  server.resetHandlers()
})

afterAll(stopMockServer)

function renderReview() {
  return render(
    <MemoryRouter>
      <Review
        gameKey="chesscom:game-1"
        userColor="white"
        gamesPath="/player/beast-ash"
      />
    </MemoryRouter>,
  )
}

async function openMistakesPanel() {
  fireEvent.click(await screen.findByRole('tab', { name: /^Mistakes/ }))
  return screen.findByRole('region', { name: /your mistakes/i })
}

describe('review', () => {
  it('renders every ply from the golden report', async () => {
    renderReview()

    expect(await screen.findByTestId('review-board')).toHaveAttribute(
      'data-fen',
      testPositions.starting_fen,
    )
    expect(
      screen.getByRole('heading', {
        name: /Adolf Anderssen vs Lionel Kieseritzky/i,
      }),
    ).toBeInTheDocument()
    expect(screen.getByText('Stockfish 19')).toBeInTheDocument()

    const moves = screen.getAllByRole('button', { name: /move \d+/i })
    expect(moves).toHaveLength(testReport.moves.length)
    expect(moves).toHaveLength(45)
  })

  it('keeps the decorative board out of the tab order', async () => {
    renderReview()
    const board = await screen.findByTestId('review-board')

    expect(board).toHaveAttribute('aria-hidden', 'true')
    expect(board).toHaveAttribute('inert')
  })

  it('keeps the board and the move list in sync', async () => {
    renderReview()
    const board = await screen.findByTestId('review-board')

    const last = screen.getByRole('button', { name: /White move 23, Be7#/i })
    fireEvent.click(last)

    expect(board).toHaveAttribute(
      'data-fen',
      testPositions.positions[44].fen_before,
    )
    expect(screen.getByTestId('mock-chessboard')).toHaveAttribute(
      'data-position',
      testPositions.positions[44].fen_before,
    )
    expect(last).toHaveAttribute('aria-current', 'true')
    expect(last).toHaveAttribute('tabindex', '0')
    expect(within(last).getByText('#')).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: 'Evaluation: M1 for White' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/23\. Be7#/)).toBeInTheDocument()
  })

  it('moves the selection with the keyboard', async () => {
    renderReview()
    const board = await screen.findByTestId('review-board')

    const first = screen.getByRole('button', { name: /White move 1, e4/ })
    first.focus()
    expect(first).toHaveFocus()

    fireEvent.keyDown(first, { key: 'ArrowRight' })
    const second = screen.getByRole('button', { name: /Black move 1, e5/ })
    expect(second).toHaveFocus()
    expect(board).toHaveAttribute(
      'data-fen',
      testPositions.positions[1].fen_before,
    )

    fireEvent.keyDown(second, { key: 'End' })
    const last = screen.getByRole('button', { name: /White move 23, Be7#/ })
    expect(last).toHaveFocus()
    expect(board).toHaveAttribute(
      'data-fen',
      testPositions.positions[44].fen_before,
    )

    fireEvent.keyDown(last, { key: 'Home' })
    expect(first).toHaveFocus()
    expect(board).toHaveAttribute('data-fen', testPositions.starting_fen)
  })

  it('steps through plies with previous and next controls', async () => {
    renderReview()
    const board = await screen.findByTestId('review-board')
    const previous = screen.getByRole('button', { name: 'Previous move' })
    const next = screen.getByRole('button', { name: 'Next move' })

    expect(previous).toBeDisabled()

    fireEvent.click(next)
    expect(board).toHaveAttribute(
      'data-fen',
      testPositions.positions[1].fen_before,
    )
    expect(previous).toBeEnabled()

    fireEvent.click(previous)
    expect(board).toHaveAttribute('data-fen', testPositions.starting_fen)
  })

  it('switches between the move list and mistakes tabs', async () => {
    renderReview()
    const movesTab = await screen.findByRole('tab', { name: 'Moves' })
    const mistakesTab = screen.getByRole('tab', { name: /^Mistakes/ })

    expect(movesTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel', { name: 'Moves' })).toBeVisible()
    expect(
      screen.queryByRole('tabpanel', { name: /^Mistakes/ }),
    ).not.toBeInTheDocument()

    fireEvent.click(mistakesTab)

    expect(mistakesTab).toHaveAttribute('aria-selected', 'true')
    expect(
      await screen.findByRole('region', { name: /your mistakes/i }),
    ).toBeVisible()
    expect(
      screen.queryByRole('tabpanel', { name: 'Moves' }),
    ).not.toBeInTheDocument()
  })

  it('moves between tabs with the keyboard', async () => {
    renderReview()
    const movesTab = await screen.findByRole('tab', { name: 'Moves' })
    movesTab.focus()

    fireEvent.keyDown(movesTab, { key: 'ArrowRight' })
    const mistakesTab = screen.getByRole('tab', { name: /^Mistakes/ })
    expect(mistakesTab).toHaveFocus()
    expect(mistakesTab).toHaveAttribute('aria-selected', 'true')

    fireEvent.keyDown(mistakesTab, { key: 'Home' })
    expect(movesTab).toHaveFocus()
    expect(movesTab).toHaveAttribute('aria-selected', 'true')
  })

  it('shows the report summary and phase breakdown', async () => {
    renderReview()
    const summary = await screen.findByRole('region', { name: /your summary/i })
    const reportSummary = testReport.summary

    expect(
      within(summary).getByText(`${reportSummary.accuracy}%`),
    ).toBeInTheDocument()
    expect(
      within(summary).getByText(String(reportSummary.blunders)),
    ).toBeInTheDocument()
    expect(
      within(summary).getByText(String(reportSummary.mistakes)),
    ).toBeInTheDocument()
    expect(
      within(summary).getByText(String(reportSummary.inaccuracies)),
    ).toBeInTheDocument()

    expect(within(summary).getByText('Opening')).toBeInTheDocument()
    expect(within(summary).getByText('2 inaccuracies')).toBeInTheDocument()
    expect(within(summary).getByText('Middlegame')).toBeInTheDocument()
    expect(within(summary).getByText('1 mistake')).toBeInTheDocument()
    expect(within(summary).getByText('Endgame')).toBeInTheDocument()
    expect(within(summary).getByText('No flagged moves')).toBeInTheDocument()
  })

  it('lists only the user’s flagged moves and their tags', async () => {
    renderReview()
    const panel = await openMistakesPanel()
    const list = within(panel).getByRole('list', { name: /flagged moves/i })

    expect(within(list).getAllByRole('button')).toHaveLength(3)
    expect(within(list).getByText('Nh4')).toBeInTheDocument()
    expect(within(list).getByText('g4')).toBeInTheDocument()
    expect(within(list).getByText('Bd6')).toBeInTheDocument()
    expect(within(list).queryByText('Na6')).not.toBeInTheDocument()
    expect(within(list).getByText('Fork')).toBeInTheDocument()
  })

  it('filters flagged moves by classification', async () => {
    renderReview()
    const panel = await openMistakesPanel()
    const all = within(panel).getByRole('button', { name: /^All/ })
    const mistakes = within(panel).getByRole('button', { name: /^Mistakes/ })

    expect(all).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(mistakes)
    expect(mistakes).toHaveAttribute('aria-pressed', 'true')

    const list = within(panel).getByRole('list', { name: /flagged moves/i })
    expect(within(list).getAllByRole('button')).toHaveLength(1)
    expect(within(list).getByText('Bd6')).toBeInTheDocument()

    fireEvent.click(within(panel).getByRole('button', { name: /^Blunders/ }))
    expect(
      within(panel).getByText(/no blunders in this game/i),
    ).toBeInTheDocument()
    expect(
      within(panel).queryByRole('list', { name: /flagged moves/i }),
    ).not.toBeInTheDocument()

    fireEvent.click(all)
    const restored = within(panel).getByRole('list', { name: /flagged moves/i })
    expect(within(restored).getAllByRole('button')).toHaveLength(3)
  })

  it('selects the ply when a flagged move is clicked', async () => {
    renderReview()
    const panel = await openMistakesPanel()
    const list = within(panel).getByRole('list', { name: /flagged moves/i })
    const bd6 = within(list).getByRole('button', { name: /Bd6/ })

    fireEvent.click(bd6)

    expect(screen.getByTestId('review-board')).toHaveAttribute(
      'data-fen',
      testPositions.positions[34].fen_before,
    )
    expect(bd6).toHaveAttribute('aria-current', 'true')
    expect(screen.getByText(/18\. Bd6/)).toBeInTheDocument()
  })

  it('draws the played and best arrows and steps through the best line', async () => {
    renderReview()
    const board = await screen.findByTestId('review-board')
    const panel = await openMistakesPanel()
    const list = within(panel).getByRole('list', { name: /flagged moves/i })
    const mock = screen.getByTestId('mock-chessboard')

    fireEvent.click(within(list).getByRole('button', { name: /Bd6/ }))

    const arrows = JSON.parse(mock.getAttribute('data-arrows') ?? '[]')
    expect(arrows).toHaveLength(2)
    expect(arrows[0]).toMatchObject({ startSquare: 'f4', endSquare: 'd6' })
    expect(arrows[1]).toMatchObject({ startSquare: 'd3', endSquare: 'd4' })
    expect(board).toHaveAttribute(
      'data-fen',
      testPositions.positions[34].fen_before,
    )

    const best = screen.getByRole('region', { name: /best line/i })
    expect(within(best).getByRole('button', { name: 'Reset' })).toBeDisabled()

    fireEvent.click(within(best).getByRole('button', { name: 'Bf8' }))

    expect(board).toHaveAttribute(
      'data-fen',
      'rnb1kbnr/p2p1ppp/8/1p1N1N1P/3PPBP1/5Q2/PqP5/R4KR1 w kq - 1 19',
    )
    expect(JSON.parse(mock.getAttribute('data-arrows') ?? '[]')).toEqual([])
    expect(
      within(best).getByText('Showing best-line move 2 of 6.'),
    ).toBeInTheDocument()

    fireEvent.click(within(best).getByRole('button', { name: 'Reset' }))

    expect(board).toHaveAttribute(
      'data-fen',
      testPositions.positions[34].fen_before,
    )
    expect(JSON.parse(mock.getAttribute('data-arrows') ?? '[]')).toHaveLength(2)
  })

  it('resets the PV preview when the selection changes', async () => {
    renderReview()
    const board = await screen.findByTestId('review-board')
    const best = await screen.findByRole('region', { name: /best line/i })

    fireEvent.click(within(best).getByRole('button', { name: 'c5' }))
    expect(board).not.toHaveAttribute(
      'data-fen',
      testPositions.starting_fen,
    )

    fireEvent.click(
      screen.getByRole('button', { name: /White move 8, Nh4/ }),
    )

    expect(board).toHaveAttribute(
      'data-fen',
      testPositions.positions[14].fen_before,
    )
    expect(
      within(screen.getByRole('region', { name: /best line/i })).getByText(
        'Showing the selected position.',
      ),
    ).toBeInTheDocument()
  })

  it('labels a missing best move and PV instead of crashing', async () => {
    const report: GameReport = {
      ...testReport,
      moves: testReport.moves.map((move) =>
        move.ply === 35 ? { ...move, best_san: '', pv_san: [] } : move,
      ),
    }
    server.use(
      http.get('*/api/games/*/report', () => HttpResponse.json(report)),
    )

    renderReview()
    const panel = await openMistakesPanel()
    const list = within(panel).getByRole('list', { name: /flagged moves/i })

    fireEvent.click(within(list).getByRole('button', { name: /Bd6/ }))

    const best = screen.getByRole('region', { name: /best line/i })
    expect(
      within(best).getByText(
        /best move is unavailable.*best line is unavailable/i,
      ),
    ).toBeInTheDocument()
    expect(screen.getByTestId('review-board')).toHaveAttribute(
      'data-fen',
      testPositions.positions[34].fen_before,
    )

    const arrows = JSON.parse(
      screen.getByTestId('mock-chessboard').getAttribute('data-arrows') ?? '[]',
    )
    expect(arrows).toHaveLength(1)
    expect(arrows[0]).toMatchObject({ startSquare: 'f4', endSquare: 'd6' })
  })

  it('notes a blunder that leaves the player ahead without softening it', async () => {
    const report: GameReport = {
      ...testReport,
      moves: testReport.moves.map((move) =>
        move.ply === 35
          ? {
              ...move,
              classification: 'blunder',
              eval_after_cp: 100,
              eval_after_win_pct: 60,
              mate_after: null,
            }
          : move,
      ),
      summary: { ...testReport.summary, blunders: 1, mistakes: 0 },
    }
    server.use(
      http.get('*/api/games/*/report', () => HttpResponse.json(report)),
    )

    renderReview()
    const panel = await openMistakesPanel()
    const list = within(panel).getByRole('list', { name: /flagged moves/i })

    expect(within(list).getByText('Still ahead (+1.0)')).toBeInTheDocument()
    expect(within(list).getByText('Blunder')).toBeInTheDocument()
  })

  it('shows a recoverable error when the report is missing', async () => {
    server.use(
      http.get('*/api/games/*/report', () =>
        HttpResponse.json({ detail: 'report not found' }, { status: 404 }),
      ),
    )

    renderReview()

    expect(
      await screen.findByRole('heading', {
        name: /this report is no longer available/i,
      }),
    ).toBeInTheDocument()

    server.resetHandlers()
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    expect(await screen.findByTestId('review-board')).toHaveAttribute(
      'data-fen',
      testPositions.starting_fen,
    )
  })

  it('refuses a report whose positions do not match', async () => {
    server.use(
      http.get('*/api/games/*/positions', () =>
        HttpResponse.json({
          ...testPositions,
          positions: testPositions.positions.slice(0, 2),
        }),
      ),
    )

    renderReview()

    expect(
      await screen.findByRole('heading', {
        name: /board data does not match/i,
      }),
    ).toBeInTheDocument()
  })
})
