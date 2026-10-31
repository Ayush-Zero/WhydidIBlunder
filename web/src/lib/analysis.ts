import type { GameSummary, Side } from '../api'

/** Navigation state used while the job page is open. Reloads still work. */
export interface AnalysisLocationState {
  game?: GameSummary
  months?: number
  reused?: boolean
  /** The filtered discovery URL to return to, when the job was started there. */
  returnTo?: string
}

export function analysisPath(username: string, jobId: string): string {
  return `/player/${encodeURIComponent(username)}/analysis/${encodeURIComponent(jobId)}`
}

export function reportPath(
  username: string,
  gameKey: string,
  userColor: Side,
): string {
  const query = new URLSearchParams({
    game_key: gameKey,
    user_color: userColor,
  })
  return `/player/${encodeURIComponent(username)}/report?${query.toString()}`
}

export function playerGamesPath(username: string): string {
  return `/player/${encodeURIComponent(username)}`
}
