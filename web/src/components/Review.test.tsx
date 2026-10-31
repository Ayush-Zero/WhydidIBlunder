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
