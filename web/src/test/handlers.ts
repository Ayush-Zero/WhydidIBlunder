import { HttpResponse, http } from 'msw'
import analyzeFixture from '../api/fixtures/analyze.json'
import cancelFixture from '../api/fixtures/cancel.json'
import gamesFixture from '../api/fixtures/games.json'
import jobFixture from '../api/fixtures/job.json'
import type {
  AnalyzeResponse,
  CancelResponse,
  GameList,
  HealthResponse,
  JobStatus,
} from '../api'

export const testJob = jobFixture as unknown as JobStatus
export const testAnalyze = analyzeFixture as unknown as AnalyzeResponse
export const testCancel = cancelFixture as unknown as CancelResponse
export const testGames = gamesFixture as unknown as GameList
export const testHealth: HealthResponse = {
  status: 'ok',
  database: 'ok',
  schema_version: 1,
}

export function makeJob(overrides: Partial<JobStatus> = {}): JobStatus {
  return {
    ...testJob,
    ...overrides,
    moves: overrides.moves ?? testJob.moves,
  }
}

/**
 * The defaults keep the app shell quiet and give each screen a happy path.
 * Individual tests replace one route with `server.use(...)`.
 */
export const defaultHandlers = [
  http.get('*/health', () => HttpResponse.json(testHealth)),
  http.get('*/api/player/:username/games', () =>
    HttpResponse.json(testGames),
  ),
  http.post('*/api/analyze', () =>
    HttpResponse.json(testAnalyze, { status: 202 }),
  ),
  http.get('*/api/jobs/:jobId', () => HttpResponse.json(testJob)),
  http.post('*/api/jobs/:jobId/cancel', () =>
    HttpResponse.json(testCancel),
  ),
]
