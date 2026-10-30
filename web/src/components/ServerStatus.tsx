import { useEffect, useState } from 'react'
import {
  checkHealth,
  errorMessage,
  isAbortError,
  isApiError,
} from '../api'

type ServerState =
  | { kind: 'checking' }
  | { kind: 'ready' }
  | { kind: 'degraded'; message: string }
  | { kind: 'offline'; message: string }

const LABELS: Record<ServerState['kind'], string> = {
  checking: 'Checking server…',
  ready: 'Server ready',
  degraded: 'Server not ready',
  offline: 'Server offline',
}

function classifyFailure(error: unknown): ServerState {
  if (
    isApiError(error) &&
    (error.kind === 'unavailable' ||
      error.kind === 'upstream' ||
      error.kind === 'server')
  ) {
    return { kind: 'degraded', message: errorMessage(error) }
  }
  return { kind: 'offline', message: errorMessage(error) }
}

/**
 * A small, honest connection indicator for the shell.
 *
 * It exists before the discovery screen so a stopped API has a visible,
 * friendly state instead of a blank page. The full screens still own their own
 * loading and error states.
 */
export function ServerStatus() {
  const [state, setState] = useState<ServerState>({ kind: 'checking' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let active = true
    const controller = new AbortController()

    void checkHealth({ signal: controller.signal })
      .then((health) => {
        if (!active) return
        setState(
          health.status === 'ok'
            ? { kind: 'ready' }
            : {
                kind: 'degraded',
                message: 'The analysis server is not ready yet. Try again shortly.',
              },
        )
      })
      .catch((error: unknown) => {
        if (!active || isAbortError(error)) return
        setState(classifyFailure(error))
      })

    return () => {
      active = false
      controller.abort()
    }
  }, [attempt])

  const detail =
    state.kind === 'ready'
      ? 'The analysis server is ready.'
      : state.kind === 'checking'
        ? 'Checking the analysis server.'
        : state.message

  return (
    <div
      className={`server-status server-status--${state.kind}`}
      role="status"
      aria-live="polite"
      title={detail}
    >
      <span className="server-status-dot" aria-hidden="true" />
      <span>{LABELS[state.kind]}</span>
      <span className="sr-only">{detail}</span>
      {state.kind === 'offline' || state.kind === 'degraded' ? (
        <button
          className="server-status-retry"
          type="button"
          onClick={() => {
            setState({ kind: 'checking' })
            setAttempt((value) => value + 1)
          }}
        >
          Retry
        </button>
      ) : null}
    </div>
  )
}
