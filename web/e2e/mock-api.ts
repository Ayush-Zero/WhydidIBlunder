import type { Page, Route } from '@playwright/test'
import type {
  AnalyzeResponse,
  CancelResponse,
  GameList,
  GamePositions,
  GameReport,
  JobStatus,
  TryRequest,
  TryResponse,
} from '../src/api'
import analyzeFixture from '../src/api/fixtures/analyze.json' with { type: 'json' }
import cancelFixture from '../src/api/fixtures/cancel.json' with { type: 'json' }
import gamesFixture from '../src/api/fixtures/games.json' with { type: 'json' }
import jobFixture from '../src/api/fixtures/job.json' with { type: 'json' }
import positionsFixture from '../src/api/fixtures/positions.json' with { type: 'json' }
import reportFixture from '../src/api/fixtures/report.json' with { type: 'json' }
import tryFixture from '../src/api/fixtures/try.json' with { type: 'json' }

const JOB_ID = 'job-e2e'
export const mockGameKey = analyzeFixture.game_key

const runningJob: JobStatus = {
  ...jobFixture,
  job_id: JOB_ID,
  game_key: mockGameKey,
  user_color: 'white',
}

const doneJob: JobStatus = {
  ...runningJob,
  status: 'done',
  progress_ply: reportFixture.moves.length,
  moves: reportFixture.moves as JobStatus['moves'],
}

const analyzeResponse: AnalyzeResponse = {
  ...analyzeFixture,
  job_id: JOB_ID,
  game_key: mockGameKey,
  user_color: 'white',
}

const cancelResponse: CancelResponse = {
  ...cancelFixture,
  job_id: JOB_ID,
}

export interface MockApiState {
  jobPolls: number
  cancelCalls: number
  tryRequests: TryRequest[]
}

export interface MockApi {
  state: MockApiState
  /** Let the next status poll return the finished job. */
  finishJob: () => void
}

/**
 * Serve the recorded API fixtures through Playwright routing.
 *
 * The app runs in a real browser against Vite, but this keeps the default E2E
 * suite network-free and engine-free. The analysis deliberately stays running
 * until the test calls `finishJob()`, so progress is asserted without a sleep.
 */
export async function installMockApi(page: Page): Promise<MockApi> {
  const state: MockApiState = {
    jobPolls: 0,
    cancelCalls: 0,
    tryRequests: [],
  }
  let finished = false

  await page.route(
    (url) => url.pathname === '/health' || url.pathname.startsWith('/api/'),
    async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      const method = request.method()

      if (method === 'GET' && path === '/health') {
        return route.fulfill({
          json: { status: 'ok', database: 'ok', schema_version: 3 },
        })
      }

      if (method === 'GET' && path === '/api/player/beast-ash/games') {
        return fulfillJson(route, gamesFixture as GameList)
      }

      if (method === 'POST' && path === '/api/analyze') {
        return fulfillJson(route, analyzeResponse)
      }

      if (method === 'GET' && /^\/api\/jobs\/[^/]+$/.test(path)) {
        state.jobPolls += 1
        return fulfillJson(route, finished ? doneJob : runningJob)
      }

      if (method === 'POST' && /^\/api\/jobs\/[^/]+\/cancel$/.test(path)) {
        state.cancelCalls += 1
        return fulfillJson(route, cancelResponse)
      }

      if (method === 'GET' && /^\/api\/games\/.+\/report$/.test(path)) {
        return fulfillJson(route, reportFixture as GameReport)
      }

      if (method === 'GET' && /^\/api\/games\/.+\/positions$/.test(path)) {
        return fulfillJson(route, positionsFixture as GamePositions)
      }

      if (method === 'POST' && /^\/api\/games\/.+\/try$/.test(path)) {
        state.tryRequests.push(request.postDataJSON() as TryRequest)
        return fulfillJson(route, tryFixture as TryResponse)
      }

      return route.fulfill({
        status: 500,
        json: { detail: `unmocked API request: ${method} ${path}` },
      })
    },
  )

  return {
    state,
    finishJob: () => {
      finished = true
    },
  }
}

async function fulfillJson(route: Route, body: unknown) {
  await route.fulfill({ json: body })
}
