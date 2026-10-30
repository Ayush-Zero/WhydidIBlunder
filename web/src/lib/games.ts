import type { GameSummary, PlayerSummary, Side, TimeClass } from '../api'

export const USERNAME_PATTERN = /^[A-Za-z0-9_-]{1,50}$/

export const MONTHS_MIN = 1
export const MONTHS_MAX = 120
export const MAX_GAMES_MIN = 1
export const MAX_GAMES_MAX = 500
export const MONTHS_DEFAULT = 3
export const MAX_GAMES_DEFAULT = 50

export const TIME_CLASSES: readonly TimeClass[] = [
  'bullet',
  'blitz',
  'rapid',
  'daily',
]

export interface DiscoveryFilters {
  months: number
  max_games: number
  time_class: TimeClass | null
  include_from_position: boolean
}

export interface DiscoverySearch extends DiscoveryFilters {
  username: string
}

export const DEFAULT_DISCOVERY_FILTERS: DiscoveryFilters = {
  months: MONTHS_DEFAULT,
  max_games: MAX_GAMES_DEFAULT,
  time_class: null,
  include_from_position: false,
}

export function isTimeClass(value: string | null): value is TimeClass {
  return value !== null && TIME_CLASSES.includes(value as TimeClass)
}

export function filtersFromSearchParams(
  params: URLSearchParams,
): DiscoveryFilters {
  return {
    months: readBoundedInteger(
      params.get('months'),
      MONTHS_DEFAULT,
      MONTHS_MIN,
      MONTHS_MAX,
    ),
    max_games: readBoundedInteger(
      params.get('max_games'),
      MAX_GAMES_DEFAULT,
      MAX_GAMES_MIN,
      MAX_GAMES_MAX,
    ),
    time_class: isTimeClass(params.get('time_class'))
      ? (params.get('time_class') as TimeClass)
      : null,
    include_from_position: params.get('include_from_position') === 'true',
  }
}

export function filtersToSearchParams(
  filters: DiscoveryFilters,
): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.months !== MONTHS_DEFAULT) {
    params.set('months', String(filters.months))
  }
  if (filters.max_games !== MAX_GAMES_DEFAULT) {
    params.set('max_games', String(filters.max_games))
  }
  if (filters.time_class) params.set('time_class', filters.time_class)
  if (filters.include_from_position) {
    params.set('include_from_position', 'true')
  }
  return params
}

export function playerPath(search: DiscoverySearch): string {
  const params = filtersToSearchParams(search)
  const query = params.toString()
  return `/player/${encodeURIComponent(search.username)}${
    query ? `?${query}` : ''
  }`
}

export function playerForGame(game: GameSummary): PlayerSummary | null {
  if (game.user_color === 'white') return game.white
  if (game.user_color === 'black') return game.black
  return null
}

export function sideLabel(side: Side | null): string {
  if (side === 'white') return 'White'
  if (side === 'black') return 'Black'
  return 'Unknown side'
}

export function timeClassLabel(timeClass: string | null): string {
  if (!timeClass) return 'Game'
  return timeClass.charAt(0).toUpperCase() + timeClass.slice(1)
}

const RESULT_LABELS: Record<string, string> = {
  '50move': '50-move draw',
  abandoned: 'Abandoned',
  agreed: 'Draw agreed',
  bughousepartnerlose: 'Partner lost',
  checkmated: 'Checkmated',
  drawn: 'Draw',
  insufficient: 'Insufficient material',
  kingofthehill: 'King of the Hill',
  losing: 'Loss',
  repetition: 'Repetition',
  resigned: 'Resigned',
  stalemate: 'Stalemate',
  threecheck: 'Three-check',
  timeout: 'Timeout',
  timevsinsufficient: 'Draw',
  win: 'Win',
}

const DRAW_RESULTS = new Set([
  'agreed',
  'drawn',
  'insufficient',
  'repetition',
  'stalemate',
  'timevsinsufficient',
  '50move',
])

export type Outcome = 'win' | 'loss' | 'draw' | 'unknown'

export function playerResultLabel(result: string | null): string {
  if (!result) return 'No result'
  return RESULT_LABELS[result] ?? result.replaceAll('_', ' ')
}

export function playerOutcome(result: string | null): Outcome {
  if (!result) return 'unknown'
  if (result === 'win') return 'win'
  if (DRAW_RESULTS.has(result)) return 'draw'
  return 'loss'
}

export function userOutcome(game: GameSummary): Outcome {
  const player = playerForGame(game)
  if (player) return playerOutcome(player.result)

  if (game.result === '1/2-1/2') return 'draw'
  if (game.result === '1-0' || game.result === '0-1') return 'unknown'
  return 'unknown'
}

export function outcomeLabel(outcome: Outcome): string {
  switch (outcome) {
    case 'win':
      return 'You won'
    case 'loss':
      return 'You lost'
    case 'draw':
      return 'Draw'
    default:
      return 'Result unknown'
  }
}

export function formatTimeControl(timeControl: string | null): string | null {
  if (!timeControl) return null

  const [base, increment] = timeControl.split('+')
  const seconds = Number(base)
  if (!Number.isFinite(seconds) || seconds < 0) return timeControl

  const baseLabel = formatSeconds(seconds)
  if (increment === undefined) return baseLabel

  const incrementSeconds = Number(increment)
  if (!Number.isFinite(incrementSeconds) || incrementSeconds < 0) {
    return timeControl
  }
  return `${baseLabel} + ${formatSeconds(incrementSeconds)}`
}

function formatSeconds(seconds: number): string {
  if (seconds >= 3_600) {
    return `${trimDecimal(seconds / 3_600)} hr`
  }
  if (seconds >= 60) {
    return `${trimDecimal(seconds / 60)} min`
  }
  return `${trimDecimal(seconds)} sec`
}

function trimDecimal(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
})

export function formatGameDate(endTime: number | null): string {
  if (endTime === null) return 'Date unknown'
  const date = new Date(endTime * 1_000)
  if (Number.isNaN(date.getTime())) return 'Date unknown'
  return dateFormatter.format(date)
}

export function gameDateIso(endTime: number | null): string | undefined {
  if (endTime === null) return undefined
  const date = new Date(endTime * 1_000)
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString()
}

export function safeChessComUrl(raw: string | null): string | null {
  if (!raw) return null
  try {
    const url = new URL(raw)
    if (url.protocol !== 'https:') return null
    const host = url.hostname.toLowerCase()
    if (host !== 'chess.com' && host !== 'www.chess.com') return null
    return url.toString()
  } catch {
    return null
  }
}

function readBoundedInteger(
  raw: string | null,
  fallback: number,
  min: number,
  max: number,
): number {
  if (raw === null) return fallback
  const value = Number(raw)
  return Number.isInteger(value) && value >= min && value <= max
    ? value
    : fallback
}
