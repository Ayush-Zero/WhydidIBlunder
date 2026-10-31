import { act, fireEvent, render, screen } from '@testing-library/react'
import { HttpResponse, http } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import {
  afterAll,
  afterEach,
  beforeAll,
  describe,
  expect,
  it,
  vi,
} from 'vitest'
import type { JobMove } from '../api'
import {
  ANALYSIS_POLL_INTERVAL_MS,
} from '../hooks/useAnalysisJob'
import { makeJob, testCancel, testJob, testPositions } from '../test/handlers'
import { server, startMockServer, stopMockServer } from '../test/server'
import { AnalysisPage } from './AnalysisPage'

const realSetTimeout = globalThis.setTimeout

beforeAll(startMockServer)

afterEach(() => {
  vi.useRealTimers()
  server.resetHandlers()
})

afterAll(stopMockServer)

function move(ply: number, san: string): JobMove {
  return {
    ...testJob.moves[0],
    ply,
    move_number: Math.ceil(ply / 2),
    san,
  }
}

function renderJob(jobId = 'job-1', state?: unknown) {
  return render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: `/player/beast-ash/analysis/${jobId}`,
          state,
        },
      ]}
    >
      <Routes>
        <Route
          path="/player/:username/analysis/:jobId"
          element={<AnalysisPage />}
        />
      </Routes>
    </MemoryRouter>,
  )
}

async function advanceClock(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

async function advancePoll(multiplier = 1) {
  await advanceClock(ANALYSIS_POLL_INTERVAL_MS * multiplier)
  // MSW dispatches the request after a zero-delay timer. Let that timer run
  // before handing control back to the fake-timer assertions.
  await advanceClock(10)
}

async function flushNetwork() {
  await act(async () => {
    for (let turn = 0; turn < 10; turn += 1) {
      await new Promise<void>((resolve) => realSetTimeout(resolve, 0))
    }
  })
}

async function waitForElement<T>(
  query: () => T,
  attempts = 80,
): Promise<T> {
  let lastError: unknown

  for (let turn = 0; turn < attempts; turn += 1) {
    try {
      return query()
    } catch (error) {
      lastError = error
    }
    await act(async () => {
      await new Promise<void>((resolve) => realSetTimeout(resolve, 1))
    })
  }

  throw lastError
}

describe('analysis lifecycle', () => {
  it('polls running jobs until done, then stops', async () => {
    vi.useFakeTimers()
    const moves = [move(1, 'e4'), move(2, 'e5')]
    let jobCalls = 0

    server.use(
      http.get('*/api/jobs/job-1', () => {
        jobCalls += 1
        return HttpResponse.json(
          makeJob({
            status: jobCalls === 1 ? 'running' : 'done',
            progress_ply: 2,
            moves,
          }),
        )
      }),
    )

    renderJob()
    await advanceClock(10)

    expect(
      await waitForElement(() =>
        screen.getByRole('heading', { name: /analysing ply 2/i }),
      ),
    ).toBeInTheDocument()
    expect(jobCalls).toBe(1)

    await advancePoll()

    expect(
      await waitForElement(() =>
        screen.getByRole('heading', { name: /analysis complete/i }),
      ),
    ).toBeInTheDocument()
    expect(screen.getByText(/2 plies evaluated and saved/i)).toBeInTheDocument()
    expect(jobCalls).toBe(2)

    // The done state mounts the C1 review, which loads the report and its
    // positions through the same job metadata.
    await advanceClock(10)
    expect(screen.getByTestId('review-board')).toHaveAttribute(
      'data-fen',
      testPositions.starting_fen,
    )

    await advanceClock(ANALYSIS_POLL_INTERVAL_MS * 3)
    expect(jobCalls).toBe(2)
  })

  it('cancels once, keeps the partial prefix, and stops polling', async () => {
    vi.useFakeTimers()
    const moves = [move(1, 'e4'), move(2, 'e5')]
    let jobCalls = 0
    let cancelCalls = 0

    server.use(
      http.get('*/api/jobs/job-1', () => {
        jobCalls += 1
        return HttpResponse.json(
          makeJob({ status: 'running', progress_ply: 2, moves }),
        )
      }),
      http.post('*/api/jobs/job-1/cancel', () => {
        cancelCalls += 1
        return HttpResponse.json(testCancel)
      }),
    )

    renderJob('job-1')
    await advanceClock(10)

    expect(
      await waitForElement(() =>
        screen.getByRole('heading', { name: /analysing ply 2/i }),
      ),
    ).toBeInTheDocument()

    const cancel = screen.getByRole('button', { name: /cancel analysis/i })
    fireEvent.click(cancel)
    fireEvent.click(cancel)
    await advanceClock(10)

    expect(
      await waitForElement(() =>
        screen.getByRole('heading', {
          name: /no further plies will be evaluated/i,
        }),
      ),
    ).toBeInTheDocument()
    expect(cancelCalls).toBe(1)
    expect(
      screen.getByText(/2 plies saved before it stopped are preserved/i),
    ).toBeInTheDocument()

    await advanceClock(ANALYSIS_POLL_INTERVAL_MS * 3)
    expect(jobCalls).toBe(1)
    expect(cancelCalls).toBe(1)
  })

  it('ignores a late poll response after cancellation', async () => {
    vi.useFakeTimers()
    const moves = [move(1, 'e4'), move(2, 'e5')]
    let jobCalls = 0
    let resolveLatePoll: ((response: Response) => void) | undefined

    server.use(
      http.get('*/api/jobs/job-1', () => {
        jobCalls += 1
        if (jobCalls === 1) {
          return HttpResponse.json(
            makeJob({ status: 'running', progress_ply: 2, moves }),
          )
        }
        return new Promise<Response>((resolve) => {
          resolveLatePoll = resolve
        })
      }),
      http.post('*/api/jobs/job-1/cancel', () =>
        HttpResponse.json(testCancel),
      ),
    )

    renderJob('job-1')
    await advanceClock(10)

    expect(
      await waitForElement(() =>
        screen.getByRole('heading', { name: /analysing ply 2/i }),
      ),
    ).toBeInTheDocument()

    await advancePoll()
    expect(jobCalls).toBe(2)
    expect(
      await waitForElement(() => {
        if (!resolveLatePoll) throw new Error('late poll has not started')
        return resolveLatePoll
      }),
    ).toBeDefined()

    fireEvent.click(screen.getByRole('button', { name: /cancel analysis/i }))
    await advanceClock(10)

    expect(
      await waitForElement(() =>
        screen.getByRole('heading', {
          name: /no further plies will be evaluated/i,
        }),
      ),
    ).toBeInTheDocument()

    await act(async () => {
      resolveLatePoll?.(
        HttpResponse.json(
          makeJob({
            status: 'running',
            progress_ply: 9,
            moves: [...moves, move(3, 'Nf3'), move(4, 'Nc6')],
          }),
        ),
      )
    })
    await flushNetwork()

    expect(
      screen.getByRole('heading', {
        name: /no further plies will be evaluated/i,
      }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: /analysing ply 9/i }),
    ).not.toBeInTheDocument()

    await advanceClock(ANALYSIS_POLL_INTERVAL_MS * 3)
    expect(jobCalls).toBe(2)
  })

  it('keeps the partial prefix when a job fails', async () => {
    vi.useFakeTimers()
    const moves = [move(1, 'e4'), move(2, 'e5')]
    let jobCalls = 0

    server.use(
      http.get('*/api/jobs/job-1', () => {
        jobCalls += 1
        return HttpResponse.json(
          makeJob(
            jobCalls === 1
              ? { status: 'running', progress_ply: 2, moves }
              : {
                  status: 'failed',
                  progress_ply: 2,
                  moves,
                  error: 'Engine crashed.',
                },
          ),
        )
      }),
    )

    renderJob()
    await advanceClock(10)

    expect(
      await waitForElement(() =>
        screen.getByRole('heading', { name: /analysing ply 2/i }),
      ),
    ).toBeInTheDocument()

    await advancePoll()

    expect(
      await waitForElement(() =>
        screen.getByRole('heading', {
          name: /worker stopped before finishing/i,
        }),
      ),
    ).toBeInTheDocument()
    expect(
      screen.getByText(
        /engine crashed\. 2 plies saved before it stopped are preserved/i,
      ),
    ).toBeInTheDocument()
    expect(jobCalls).toBe(2)

    await advanceClock(ANALYSIS_POLL_INTERVAL_MS * 3)
    expect(jobCalls).toBe(2)
  })

  it('stops polling when the page unmounts', async () => {
    vi.useFakeTimers()
    let jobCalls = 0

    server.use(
      http.get('*/api/jobs/job-1', () => {
        jobCalls += 1
        return HttpResponse.json(makeJob({ status: 'running', progress_ply: 1 }))
      }),
    )

    const view = renderJob()
    await advanceClock(10)

    expect(
      await waitForElement(() =>
        screen.getByRole('heading', { name: /analysing ply 1/i }),
      ),
    ).toBeInTheDocument()
    expect(jobCalls).toBe(1)

    view.unmount()
    await advanceClock(ANALYSIS_POLL_INTERVAL_MS * 3)
    expect(jobCalls).toBe(1)
  })

  it('returns to the filtered discovery URL when one was supplied', async () => {
    vi.useFakeTimers()
    server.use(
      http.get('*/api/jobs/job-1', () =>
        HttpResponse.json(makeJob({ status: 'done', progress_ply: 2 })),
      ),
    )

    renderJob('job-1', {
      returnTo: '/player/beast-ash?months=6&time_class=blitz',
    })
    await advanceClock(10)

    expect(
      await waitForElement(() =>
        screen.getByRole('heading', { name: /analysis complete/i }),
      ),
    ).toBeInTheDocument()

    for (const link of screen.getAllByRole('link', { name: /back to games/i })) {
      expect(link).toHaveAttribute(
        'href',
        '/player/beast-ash?months=6&time_class=blitz',
      )
    }
  })

  it('shows a recoverable error when the job is gone', async () => {
    vi.useFakeTimers()
    server.use(
      http.get('*/api/jobs/missing', () =>
        HttpResponse.json({ detail: 'job not found' }, { status: 404 }),
      ),
    )

    renderJob('missing')
    await advanceClock(10)

    expect(
      await waitForElement(() =>
        screen.getByRole('heading', {
          name: /this analysis is no longer available/i,
        }),
      ),
    ).toBeInTheDocument()
    expect(screen.getByText(/job not found/i)).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /retry/i }),
    ).not.toBeInTheDocument()
    expect(
      screen.getAllByRole('link', { name: /back to games/i }),
    ).not.toHaveLength(0)
  })
})
