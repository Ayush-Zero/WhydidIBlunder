import { describe, expect, it } from 'vitest'
import type { GameSummary } from '../api'
import gamesFixture from '../api/fixtures/games.json'
import {
  filtersFromSearchParams,
  filtersToSearchParams,
  formatTimeControl,
  playerPath,
  safeChessComUrl,
  userOutcome,
} from './games'

describe('discovery helpers', () => {
  it('reads and writes bounded filter values', () => {
    expect(filtersFromSearchParams(new URLSearchParams())).toEqual({
      months: 3,
      max_games: 50,
      time_class: null,
      include_from_position: false,
    })

    expect(
      filtersFromSearchParams(
        new URLSearchParams({
          months: '6',
          max_games: '20',
          time_class: 'blitz',
          include_from_position: 'true',
        }),
      ),
    ).toEqual({
      months: 6,
      max_games: 20,
      time_class: 'blitz',
      include_from_position: true,
    })

    expect(
      filtersFromSearchParams(
        new URLSearchParams({
          months: '0',
          max_games: '501',
          time_class: 'ultrabullet',
        }),
      ),
    ).toEqual({
      months: 3,
      max_games: 50,
      time_class: null,
      include_from_position: false,
    })
  })

  it('builds a clean player URL', () => {
    expect(
      playerPath({
        username: 'beast-ash',
        months: 3,
        max_games: 50,
        time_class: null,
        include_from_position: false,
      }),
    ).toBe('/player/beast-ash')

    expect(
      filtersToSearchParams({
        months: 6,
        max_games: 20,
        time_class: 'blitz',
        include_from_position: true,
      }).toString(),
    ).toBe('months=6&max_games=20&time_class=blitz&include_from_position=true')
  })

  it('formats common time controls', () => {
    expect(formatTimeControl('600')).toBe('10 min')
    expect(formatTimeControl('180+2')).toBe('3 min + 2 sec')
    expect(formatTimeControl('1/86400')).toBe('1/86400')
    expect(formatTimeControl(null)).toBeNull()
  })

  it('only links to HTTPS Chess.com pages', () => {
    expect(
      safeChessComUrl('https://www.chess.com/game/live/184741950578'),
    ).toBe('https://www.chess.com/game/live/184741950578')
    expect(safeChessComUrl('http://www.chess.com/game/live/1')).toBeNull()
    expect(safeChessComUrl('https://example.com/game/1')).toBeNull()
    expect(safeChessComUrl('javascript:alert(1)')).toBeNull()
    expect(safeChessComUrl(null)).toBeNull()
  })

  it('reads the requested player result from either colour', () => {
    const whiteGame = gamesFixture.games[0] as GameSummary
    const blackGame = gamesFixture.games[1] as GameSummary

    expect(userOutcome(whiteGame)).toBe('win')
    expect(userOutcome(blackGame)).toBe('win')
  })
})
