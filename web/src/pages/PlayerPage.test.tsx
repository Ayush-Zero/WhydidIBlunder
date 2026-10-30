import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import gamesFixture from '../api/fixtures/games.json'
import { jsonResponse } from '../test/http'
import { PlayerPage } from './PlayerPage'

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function respond(body: unknown, status = 200) {
  fetchMock.mockResolvedValueOnce(jsonResponse(body, { status }))
}

function renderPlayerPage(username: string, search = '') {
  return render(
    <MemoryRouter initialEntries={[`/player/${username}${search}`]}>
      <Routes>
        <Route path="/player/:username" element={<PlayerPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

function requestUrl(index = -1): string {
  const call = fetchMock.mock.calls.at(index)
  if (!call) throw new Error('fetch was not called')
  const [input] = call
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.href
  return input.url
}

function fromPositionGame() {
  return {
    ...gamesFixture.games[0],
    is_bot: true,
    from_position: true,
  }
}

describe('player discovery', () => {
  it('renders games with ratings, results and badges', async () => {
    respond(gamesFixture)

    renderPlayerPage('beast-ash')

    expect(
      await screen.findAllByRole('heading', { level: 3 }),
    ).toHaveLength(2)
    expect(
      screen.getByRole('heading', { name: /games for beast-ash/i }),
    ).toBeInTheDocument()
    expect(screen.getByText('2', { selector: '.result-count strong' })).toBeInTheDocument()
    expect(screen.getAllByText('BEAST-ASH')).toHaveLength(2)
    expect(screen.getByText('1171')).toBeInTheDocument()
    expect(screen.getByText('1153')).toBeInTheDocument()
    expect(screen.getAllByText('You won')).toHaveLength(2)
    expect(screen.getAllByText('Rapid · Rated')).toHaveLength(2)
    expect(screen.getAllByText(/Rapid · 10 min/)).toHaveLength(2)

    const analyzeButtons = screen.getAllByRole('button', { name: /^analyze$/i })
    expect(analyzeButtons).toHaveLength(2)
    for (const button of analyzeButtons) {
      expect(button).toBeEnabled()
    }

    expect(
      screen.getAllByRole('link', { name: /open game on chess\.com/i }),
    ).toHaveLength(2)
    expect(requestUrl()).toBe(
      '/api/player/beast-ash/games?months=3&max_games=50',
    )
  })

  it('shows the loading state while the game list is pending', async () => {
    let resolveFetch: ((response: Response) => void) | undefined
    fetchMock.mockReturnValueOnce(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve
      }),
    )

    renderPlayerPage('beast-ash')

    expect(screen.getByRole('status')).toHaveTextContent(
      /loading games for beast-ash/i,
    )
    resolveFetch?.(jsonResponse(gamesFixture))
    expect(
      await screen.findAllByRole('heading', { level: 3 }),
    ).toHaveLength(2)
  })

  it('shows a player-not-found error for a 404', async () => {
    respond({ detail: "no chess.com player named 'nobody'" }, 404)

    renderPlayerPage('nobody')

    expect(
      await screen.findByRole('heading', { name: /player not found/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/no chess\.com player named 'nobody'/i),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /retry/i }),
    ).not.toBeInTheDocument()
  })

  it('shows a friendly upstream error for a 502', async () => {
    respond({ detail: 'chess.com request failed: rate limited' }, 502)

    renderPlayerPage('beast-ash')

    expect(
      await screen.findByRole('heading', {
        name: /chess\.com is not responding/i,
      }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/chess\.com is not responding right now/i),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
  })

  it('shows an empty state when the range has no games', async () => {
    respond({ username: 'beast-ash', count: 0, games: [] })

    renderPlayerPage('beast-ash')

    expect(
      await screen.findByRole('heading', { name: /no games in that range/i }),
    ).toBeInTheDocument()
    expect(screen.getByText(/try another username/i)).toBeInTheDocument()
  })

  it('disables Analyze for a custom-start game and explains why', async () => {
    respond({
      username: 'beast-ash',
      count: 1,
      games: [fromPositionGame()],
    })

    renderPlayerPage('beast-ash')

    const analyze = await screen.findByRole('button', { name: /^analyze$/i })
    expect(analyze).toBeDisabled()
    expect(analyze).toHaveAttribute('aria-describedby')
    expect(
      screen.getByText(/custom-start games cannot be analysed: they have no ply 1/i),
    ).toBeInTheDocument()
    expect(screen.getByText('Bot')).toBeInTheDocument()
    expect(screen.getByText('Custom start')).toBeInTheDocument()
  })

  it('applies the time-class, months and max-games controls', async () => {
    respond(gamesFixture)
    respond(gamesFixture)

    renderPlayerPage('beast-ash')
    await screen.findAllByRole('heading', { level: 3 })

    const user = userEvent.setup()
    const months = screen.getByLabelText(/months/i)
    await user.clear(months)
    await user.type(months, '6')

    const maxGames = screen.getByLabelText(/max games/i)
    await user.clear(maxGames)
    await user.type(maxGames, '20')

    await user.selectOptions(screen.getByLabelText(/time class/i), 'blitz')
    await user.click(screen.getByLabelText(/show custom-start games/i))
    await user.click(screen.getByRole('button', { name: /update games/i }))

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    expect(requestUrl()).toBe(
      '/api/player/beast-ash/games?months=6&max_games=20&time_class=blitz&include_from_position=true',
    )
  })

  it('retries a failed game-list request', async () => {
    respond({ detail: 'upstream failure' }, 502)
    respond(gamesFixture)

    renderPlayerPage('beast-ash')

    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /retry/i }))

    expect(
      await screen.findAllByRole('heading', { level: 3 }),
    ).toHaveLength(2)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
