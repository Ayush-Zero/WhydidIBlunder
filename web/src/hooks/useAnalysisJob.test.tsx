import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api'
import { makeJob, testCancel } from '../test/handlers'
import {
  ANALYSIS_POLL_INTERVAL_MS,
  useAnalysisJob,
} from './useAnalysisJob'

function Harness({ jobId = 'job-1' }: { jobId?: string }) {
  const analysis = useAnalysisJob(jobId)

  return (
    <div>
      <span data-testid="phase">{analysis.phase}</span>
      <span data-testid="plies">{analysis.job?.moves.length ?? 0}</span>
      <button type="button" onClick={analysis.cancel}>
        Cancel
      </button>
    </div>
  )
}

async function flushPromises() {
  await act(async () => {
    await Promise.resolve()
  })
}

async function advance(ms = ANALYSIS_POLL_INTERVAL_MS) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useAnalysisJob scheduler', () => {
  it('polls on the interval and stops at a terminal state', async () => {
    const getJob = vi
      .spyOn(api, 'getJob')
      .mockResolvedValueOnce(
        makeJob({ status: 'running', progress_ply: 1 }),
      )
      .mockResolvedValueOnce(makeJob({ status: 'done', progress_ply: 1 }))

    render(<Harness />)
    await flushPromises()

    expect(screen.getByTestId('phase')).toHaveTextContent('running')
    expect(getJob).toHaveBeenCalledTimes(1)

    await advance()

    expect(screen.getByTestId('phase')).toHaveTextContent('done')
    expect(getJob).toHaveBeenCalledTimes(2)

    await advance(ANALYSIS_POLL_INTERVAL_MS * 3)

    expect(getJob).toHaveBeenCalledTimes(2)
  })

  it('cancels once and never polls again', async () => {
    const getJob = vi
      .spyOn(api, 'getJob')
      .mockResolvedValue(makeJob({ status: 'running', progress_ply: 1 }))
    const cancelJob = vi.spyOn(api, 'cancelJob').mockResolvedValue(testCancel)

    render(<Harness />)
    await flushPromises()

    expect(screen.getByTestId('phase')).toHaveTextContent('running')

    const cancel = screen.getByRole('button', { name: /cancel/i })
    fireEvent.click(cancel)
    fireEvent.click(cancel)
    await flushPromises()

    expect(screen.getByTestId('phase')).toHaveTextContent('cancelled')
    expect(cancelJob).toHaveBeenCalledTimes(1)

    await advance(ANALYSIS_POLL_INTERVAL_MS * 3)

    expect(getJob).toHaveBeenCalledTimes(1)
    expect(cancelJob).toHaveBeenCalledTimes(1)
  })

  it('stops polling when it unmounts', async () => {
    const getJob = vi
      .spyOn(api, 'getJob')
      .mockResolvedValue(makeJob({ status: 'running', progress_ply: 1 }))

    const view = render(<Harness />)
    await flushPromises()

    expect(getJob).toHaveBeenCalledTimes(1)

    view.unmount()
    await advance(ANALYSIS_POLL_INTERVAL_MS * 3)

    expect(getJob).toHaveBeenCalledTimes(1)
  })
})
