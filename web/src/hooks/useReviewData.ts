import { useCallback, useEffect, useState } from 'react'
import {
  api,
  isAbortError,
  type GamePositions,
  type GameReport,
  type Side,
} from '../api'

export type ReviewPhase = 'loading' | 'ready' | 'error'

export interface ReviewData {
  phase: ReviewPhase
  report: GameReport | null
  positions: GamePositions | null
  error: unknown | null
}

export interface ReviewDataController extends ReviewData {
  retry: () => void
}

const INITIAL_STATE: ReviewData = {
  phase: 'loading',
  report: null,
  positions: null,
  error: null,
}

interface StoredState {
  requestKey: string
  data: ReviewData
}

/**
 * Load the frozen report and its board positions together.
 *
 * A request key makes retries (and a changed game) derive as loading without a
 * synchronous state write in the effect. Both requests share one abort
 * controller, so a late response cannot replace newer data.
 */
export function useReviewData(
  gameKey: string,
  userColor: Side,
): ReviewDataController {
  const [stored, setStored] = useState<StoredState>({
    requestKey: '',
    data: INITIAL_STATE,
  })
  const [retryToken, setRetryToken] = useState(0)
  const requestKey = `${gameKey}\u0000${userColor}\u0000${retryToken}`
  const data = stored.requestKey === requestKey ? stored.data : INITIAL_STATE

  useEffect(() => {
    const controller = new AbortController()
    let active = true

    Promise.all([
      api.getReport(gameKey, userColor, { signal: controller.signal }),
      api.getPositions(gameKey, userColor, { signal: controller.signal }),
    ])
      .then(([report, positions]) => {
        if (!active) return
        setStored({
          requestKey,
          data: { phase: 'ready', report, positions, error: null },
        })
      })
      .catch((error: unknown) => {
        if (!active || isAbortError(error)) return
        setStored({
          requestKey,
          data: { phase: 'error', report: null, positions: null, error },
        })
      })

    return () => {
      active = false
      controller.abort()
    }
  }, [gameKey, userColor, requestKey])

  const retry = useCallback(() => {
    setRetryToken((value) => value + 1)
  }, [])

  return { ...data, retry }
}
