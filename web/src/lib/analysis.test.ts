import { describe, expect, it } from 'vitest'
import { analysisPath, playerGamesPath, reportPath } from './analysis'

describe('analysis routes', () => {
  it('encodes opaque report keys in the query string', () => {
    expect(reportPath('beast-ash', 'chesscom:abc/def', 'black')).toBe(
      '/player/beast-ash/report?game_key=chesscom%3Aabc%2Fdef&user_color=black',
    )
  })

  it('keeps job ids and usernames in their own path segments', () => {
    expect(analysisPath('beast/ash', 'job 1')).toBe(
      '/player/beast%2Fash/analysis/job%201',
    )
    expect(playerGamesPath('beast/ash')).toBe('/player/beast%2Fash')
  })
})
