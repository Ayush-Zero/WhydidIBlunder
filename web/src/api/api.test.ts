import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, fetchJson } from './index'
import analyzeFixture from './fixtures/analyze.json'
import cancelFixture from './fixtures/cancel.json'
import gamesFixture from './fixtures/games.json'
import jobFixture from './fixtures/job.json'
import positionsFixture from './fixtures/positions.json'
import reportFixture from './fixtures/report.json'
import tryFixture from './fixtures/try.json'
import { jsonResponse } from '../test/http'

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function respond(
  body: unknown,
  status = 200,
  headers: Record<string, string> = {},
) {
  fetchMock.mockResolvedValueOnce(jsonResponse(body, { status, headers }))
}

function lastCall() {
  const call = fetchMock.mock.calls.at(-1)
  if (!call) throw new Error('fetch was not called')
  const [input, init] = call
  const url =
    typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.href
        : input.url
  return { url, init: init ?? {} }
}

async function captureApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (error) {
    if (error instanceof ApiError) return error
    throw error
  }
  throw new Error('expected the request to fail')
}

describe('typed API client', () => {
  it('parses C1 and sends only the supplied filters', async () => {
    respond(gamesFixture)

    const result = await api.listGames('beast-ash', {
      months: 1,
      time_class: 'rapid',
    })

    expect(result).toEqual(gamesFixture)
    expect(lastCall().url).toBe(
      '/api/player/beast-ash/games?months=1&time_class=rapid',
    )
  })

  it('parses C2 and posts the JSON body', async () => {
    const request = {
      username: 'beast-ash',
      game_key: gamesFixture.games[0].game_key,
    }
    respond(analyzeFixture, 202)

    const result = await api.analyzeGame(request)

    expect(result).toEqual(analyzeFixture)
    const { url, init } = lastCall()
    expect(url).toBe('/api/analyze')
    expect(init.method).toBe('POST')
    expect(init.body).toBe(JSON.stringify(request))
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
    })
  })

  it('parses C3 job progress', async () => {
    respond(jobFixture)

    const result = await api.getJob('job-1')

    expect(result).toEqual(jobFixture)
    expect(lastCall().url).toBe('/api/jobs/job-1')
  })

  it('parses C4 cancel and uses POST', async () => {
    respond(cancelFixture)

    const result = await api.cancelJob('job-1')

    expect(result).toEqual(cancelFixture)
    expect(lastCall().url).toBe('/api/jobs/job-1/cancel')
    expect(lastCall().init.method).toBe('POST')
  })

  it('parses C5 and encodes an opaque game key', async () => {
    const gameKey =
      'chesscom-url:https://www.chess.com/game/daily/123?x=1#frag'
    respond(reportFixture)

    const result = await api.getReport(gameKey, 'white')

    expect(result.schema_version).toBe(1)
    expect(result.summary.user_color).toBe('white')
    expect(lastCall().url).toBe(
      '/api/games/chesscom-url:https://www.chess.com/game/daily/123%3Fx%3D1%23frag/report?user_color=white',
    )
  })

  it('parses A1 positions and asks for the right colour', async () => {
    respond(positionsFixture)

    const result = await api.getPositions('chesscom:game-1', 'white')

    expect(result).toEqual(positionsFixture)
    expect(lastCall().url).toBe(
      '/api/games/chesscom:game-1/positions?user_color=white',
    )
  })

  it('parses D1 try-again grading and posts the candidate', async () => {
    const request = { user_color: 'white' as const, ply: 4, move_san: 'Qh5' }
    respond(tryFixture)

    const result = await api.tryMove('chesscom:game-1', request)

    expect(result).toEqual(tryFixture)
    expect(lastCall().url).toBe('/api/games/chesscom:game-1/try')
    expect(lastCall().init.method).toBe('POST')
    expect(lastCall().init.body).toBe(JSON.stringify(request))
  })

  it('checks /health at the origin, not under /api', async () => {
    respond({ status: 'ok', database: 'ok', schema_version: 1 })

    const result = await api.checkHealth()

    expect(result.status).toBe('ok')
    expect(lastCall().url).toBe('/health')
  })
})

describe('transport failures', () => {
  it.each([
    [404, 'not-found'],
    [409, 'conflict'],
    [422, 'validation'],
    [429, 'rate-limited'],
    [502, 'upstream'],
    [503, 'unavailable'],
    [504, 'timeout'],
    [500, 'server'],
  ] as const)('maps HTTP %i to %s', async (status, kind) => {
    respond({ detail: 'request failed' }, status)

    const error = await captureApiError(api.getJob('job-1'))

    expect(error.kind).toBe(kind)
    expect(error.status).toBe(status)
  })

  it('keeps the Retry-After value on 429', async () => {
    respond({ detail: 'slow down' }, 429, { 'Retry-After': '120' })

    const error = await captureApiError(api.getJob('job-1'))

    expect(error.message).toBe('slow down')
    expect(error.retryAfterSeconds).toBe(120)
  })

  it('uses a friendly 422 message when FastAPI returns an array', async () => {
    respond({ detail: [{ msg: 'bad colour' }] }, 422)

    const error = await captureApiError(api.getJob('job-1'))

    expect(error.kind).toBe('validation')
    expect(error.detail).toBeNull()
    expect(error.message).toMatch(/request was not valid/i)
  })

  it('turns a stopped API into a friendly network error', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))

    const error = await captureApiError(fetchJson('/api/games'))

    expect(error.kind).toBe('network')
    expect(error.status).toBeNull()
    expect(error.message).toMatch(/can't reach the analysis server/i)
  })

  it('rejects malformed JSON as an invalid response', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response('not json', {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    const error = await captureApiError(fetchJson('/api/games'))

    expect(error.kind).toBe('invalid-response')
    expect(error.message).toMatch(/unexpected response/i)
  })

  it('distinguishes a caller abort from a timeout', async () => {
    fetchMock.mockImplementation(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'))
          })
        }),
    )

    const controller = new AbortController()
    const pending = fetchJson('/api/slow', { signal: controller.signal })
    const assertion = expect(pending).rejects.toMatchObject({ kind: 'aborted' })
    controller.abort()
    await assertion
  })

  it('times out a hung request', async () => {
    vi.useFakeTimers()
    fetchMock.mockImplementation(
      (_input, init) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'))
          })
        }),
    )

    const pending = fetchJson('/api/slow', { timeoutMs: 25 })
    const assertion = expect(pending).rejects.toMatchObject({ kind: 'timeout' })
    await vi.advanceTimersByTimeAsync(25)
    await assertion
  })
})
