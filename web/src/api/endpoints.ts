import {
  fetchJson,
  type RequestConfig,
} from './http'
import type {
  AnalyzeRequest,
  AnalyzeResponse,
  CancelResponse,
  GameList,
  GameListQuery,
  GamePositions,
  GameReport,
  HealthResponse,
  JobStatus,
  Side,
  TryRequest,
  TryResponse,
} from './types'
import { apiUrl, encodeGameKey, serverUrl } from './url'

export function listGames(
  username: string,
  query: GameListQuery = {},
  config: RequestConfig = {},
): Promise<GameList> {
  const params = new URLSearchParams()
  if (query.months !== undefined) params.set('months', String(query.months))
  if (query.max_games !== undefined) {
    params.set('max_games', String(query.max_games))
  }
  if (query.time_class !== undefined) {
    params.set('time_class', query.time_class)
  }
  if (query.include_bots !== undefined) {
    params.set('include_bots', String(query.include_bots))
  }
  if (query.include_from_position !== undefined) {
    params.set('include_from_position', String(query.include_from_position))
  }

  const search = params.toString()
  return fetchJson<GameList>(
    apiUrl(`/player/${encodeURIComponent(username)}/games${search ? `?${search}` : ''}`),
    config,
  )
}

export function analyzeGame(
  request: AnalyzeRequest,
  config: RequestConfig = {},
): Promise<AnalyzeResponse> {
  return fetchJson<AnalyzeResponse>(apiUrl('/analyze'), {
    ...config,
    method: 'POST',
    body: request,
  })
}

export function getJob(
  jobId: string,
  config: RequestConfig = {},
): Promise<JobStatus> {
  return fetchJson<JobStatus>(apiUrl(`/jobs/${encodeURIComponent(jobId)}`), config)
}

export function cancelJob(
  jobId: string,
  config: RequestConfig = {},
): Promise<CancelResponse> {
  return fetchJson<CancelResponse>(
    apiUrl(`/jobs/${encodeURIComponent(jobId)}/cancel`),
    {
      ...config,
      method: 'POST',
    },
  )
}

export function getReport(
  gameKey: string,
  userColor: Side,
  config: RequestConfig = {},
): Promise<GameReport> {
  const query = new URLSearchParams({ user_color: userColor })
  return fetchJson<GameReport>(
    apiUrl(`/games/${encodeGameKey(gameKey)}/report?${query.toString()}`),
    config,
  )
}

export function getPositions(
  gameKey: string,
  userColor: Side,
  config: RequestConfig = {},
): Promise<GamePositions> {
  const query = new URLSearchParams({ user_color: userColor })
  return fetchJson<GamePositions>(
    apiUrl(`/games/${encodeGameKey(gameKey)}/positions?${query.toString()}`),
    config,
  )
}

export function tryMove(
  gameKey: string,
  request: TryRequest,
  config: RequestConfig = {},
): Promise<TryResponse> {
  return fetchJson<TryResponse>(apiUrl(`/games/${encodeGameKey(gameKey)}/try`), {
    ...config,
    method: 'POST',
    body: request,
  })
}

/** /health lives at the API origin, outside the /api prefix. */
export function checkHealth(
  config: RequestConfig = {},
): Promise<HealthResponse> {
  return fetchJson<HealthResponse>(serverUrl('/health'), {
    timeoutMs: 5_000,
    ...config,
  })
}

export const api = {
  listGames,
  analyzeGame,
  getJob,
  cancelJob,
  getReport,
  getPositions,
  tryMove,
  checkHealth,
} as const
