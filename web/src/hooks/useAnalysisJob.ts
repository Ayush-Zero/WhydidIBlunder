import { useCallback, useEffect, useRef, useState } from 'react'
import {
  api,
  isAbortError,
  type JobStatus,
  type JobStatusValue,
} from '../api'

export const ANALYSIS_POLL_INTERVAL_MS = 1_500

export type AnalysisPhase = 'loading' | JobStatusValue | 'error'

export interface AnalysisState {
  phase: AnalysisPhase
  job: JobStatus | null
  error: unknown | null
  cancelling: boolean
  cancelError: unknown | null
}

export interface AnalysisJobController extends AnalysisState {
  retry: () => void
  cancel: () => void
}

const INITIAL_STATE: AnalysisState = {
  phase: 'loading',
  job: null,
  error: null,
  cancelling: false,
  cancelError: null,
}

interface StoredState {
  requestKey: string
  state: AnalysisState
}

function isTerminalPhase(phase: AnalysisPhase): boolean {
  return phase === 'done' || phase === 'failed' || phase === 'cancelled'
}

function isActivePhase(
  phase: AnalysisPhase,
): phase is Extract<JobStatusValue, 'queued' | 'running'> {
  return phase === 'queued' || phase === 'running'
}

/**
 * Poll one C3 job until it reaches a terminal state.
 *
 * A single recursive timeout keeps requests serial: the next poll is scheduled
 * only after the previous response. Cancel pauses the loop and aborts any
 * in-flight request, so a late response cannot replace the cancelled state.
 */
export function useAnalysisJob(jobId: string): AnalysisJobController {
  const [stored, setStored] = useState<StoredState>({
    requestKey: '',
    state: INITIAL_STATE,
  })
  const [retryToken, setRetryToken] = useState(0)
  const requestKey = `${jobId}\u0000${retryToken}`
  const state = stored.requestKey === requestKey ? stored.state : INITIAL_STATE

  const pausedRef = useRef(false)
  const timerRef = useRef<number | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const activeJobRef = useRef(jobId)
  const cancellingRef = useRef(false)
  const mountedRef = useRef(false)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (activeJobRef.current !== jobId) {
      activeJobRef.current = jobId
      pausedRef.current = false
      cancellingRef.current = false
    }
    if (pausedRef.current) return

    let stopped = false
    let timer: number | null = null

    const commit = (update: (previous: AnalysisState) => AnalysisState) => {
      setStored((previous) => ({
        requestKey,
        state: update(
          previous.requestKey === requestKey ? previous.state : INITIAL_STATE,
        ),
      }))
    }

    async function poll() {
      if (stopped || pausedRef.current) return

      const controller = new AbortController()
      abortRef.current = controller

      try {
        const job = await api.getJob(jobId, { signal: controller.signal })
        if (stopped || pausedRef.current || controller.signal.aborted) return

        commit((previous) =>
          isTerminalPhase(previous.phase)
            ? previous
            : { ...previous, phase: job.status, job, error: null },
        )

        if (job.status === 'queued' || job.status === 'running') {
          const timeoutId = setTimeout(() => {
            timer = null
            if (timerRef.current === timeoutId) timerRef.current = null
            void poll()
          }, ANALYSIS_POLL_INTERVAL_MS)
          timer = timeoutId
          timerRef.current = timeoutId
        }
      } catch (error) {
        if (stopped || pausedRef.current || isAbortError(error)) return
        commit((previous) => ({ ...previous, phase: 'error', error }))
      } finally {
        if (abortRef.current === controller) abortRef.current = null
      }
    }

    void poll()

    return () => {
      stopped = true
      if (timer !== null) {
        clearTimeout(timer)
        if (timerRef.current === timer) timerRef.current = null
      }
      abortRef.current?.abort()
      abortRef.current = null
    }
  }, [jobId, requestKey])

  const retry = useCallback(() => {
    pausedRef.current = false
    setRetryToken((value) => value + 1)
  }, [])

  const cancel = useCallback(() => {
    if (cancellingRef.current || !isActivePhase(state.phase)) return

    cancellingRef.current = true
    pausedRef.current = true
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    abortRef.current?.abort()
    abortRef.current = null

    setStored((previous) =>
      previous.requestKey === requestKey
        ? {
            requestKey,
            state: {
              ...previous.state,
              cancelling: true,
              cancelError: null,
            },
          }
        : previous,
    )

    api
      .cancelJob(jobId)
      .then((response) => {
        if (!mountedRef.current || activeJobRef.current !== jobId) return
        cancellingRef.current = false

        if (response.cancelled || response.status === 'cancelled') {
          commitCancelResponse('cancelled')
          return
        }

        if (response.status === 'done' || response.status === 'failed') {
          commitCancelResponse(response.status)
          return
        }

        pausedRef.current = false
        setStored((previous) =>
          previous.requestKey === requestKey
            ? {
                requestKey,
                state: { ...previous.state, cancelling: false },
              }
            : previous,
        )
        setRetryToken((value) => value + 1)
      })
      .catch((error: unknown) => {
        if (!mountedRef.current || activeJobRef.current !== jobId) return
        cancellingRef.current = false
        if (isAbortError(error)) return

        pausedRef.current = false
        setStored((previous) =>
          previous.requestKey === requestKey
            ? {
                requestKey,
                state: {
                  ...previous.state,
                  cancelling: false,
                  cancelError: error,
                },
              }
            : previous,
        )
        setRetryToken((value) => value + 1)
      })

    function commitCancelResponse(phase: 'done' | 'failed' | 'cancelled') {
      setStored((previous) => {
        if (previous.requestKey !== requestKey) return previous
        const job = previous.state.job
        const nextJob = job
          ? {
              ...job,
              status: phase,
              error: phase === 'cancelled' ? null : job.error,
            }
          : null
        return {
          requestKey,
          state: {
            ...previous.state,
            phase,
            job: nextJob,
            cancelling: false,
            cancelError: null,
          },
        }
      })
    }
  }, [jobId, requestKey, state.phase])

  return { ...state, retry, cancel }
}
