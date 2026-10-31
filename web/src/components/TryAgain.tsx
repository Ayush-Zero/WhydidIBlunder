import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  api,
  errorMessage,
  isAbortError,
  type MoveEvaluation,
  type Side,
  type TryResponse,
} from '../api'
import { CLASSIFICATION_LABELS, legalSanMoves } from '../lib/review'

/** The server's try timeout defaults to 20s; allow it to finish first. */
const TRY_TIMEOUT_MS = 25_000

interface TryAgainProps {
  gameKey: string
  userColor: Side
  move: MoveEvaluation
  fenBefore: string
  onReset: () => void
}

/**
 * Grade a replacement move for one flagged ply.
 *
 * The select is built from the report's position, so illegal moves cannot be
 * submitted from the UI. The server still reconstructs the position and
 * re-validates the SAN; this component only renders its result. The report is
 * never mutated, and reset closes the form without changing the review.
 */
export function TryAgain({
  gameKey,
  userColor,
  move,
  fenBefore,
  onReset,
}: TryAgainProps) {
  const legalMoves = legalSanMoves(fenBefore)
  const [candidate, setCandidate] = useState('')
  const [pending, setPending] = useState(false)
  const [result, setResult] = useState<TryResponse | null>(null)
  const [error, setError] = useState<unknown | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => abortRef.current?.abort()
  }, [])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!candidate || pending) return

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setPending(true)
    setResult(null)
    setError(null)

    try {
      const response = await api.tryMove(
        gameKey,
        {
          user_color: userColor,
          ply: move.ply,
          move_san: candidate,
        },
        { signal: controller.signal, timeoutMs: TRY_TIMEOUT_MS },
      )

      if (!controller.signal.aborted) setResult(response)
    } catch (cause) {
      if (!isAbortError(cause)) setError(cause)
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
        if (!controller.signal.aborted) setPending(false)
      }
    }
  }

  function tryAnother() {
    setCandidate('')
    setResult(null)
    setError(null)
  }

  return (
    <section
      id="try-again-panel"
      className="try-again"
      aria-labelledby="try-again-title"
      aria-busy={pending}
    >
      <div className="try-again-header">
        <h3 id="try-again-title">Try again</h3>
        <p>Find a better move than {move.san} from this position.</p>
      </div>

      {result ? (
        <TryResult
          result={result}
          candidate={candidate}
          onAnother={tryAnother}
          onReset={onReset}
        />
      ) : (
        <form className="try-again-form" onSubmit={handleSubmit}>
          <label className="try-again-label" htmlFor="try-again-move">
            Your move
          </label>

          {legalMoves.length > 0 ? (
            <div className="try-again-controls">
              <select
                id="try-again-move"
                value={candidate}
                disabled={pending}
                onChange={(event) => setCandidate(event.target.value)}
              >
                <option value="">Choose a legal move</option>
                {legalMoves.map((san) => (
                  <option key={san} value={san}>
                    {san}
                  </option>
                ))}
              </select>
              <button
                className="button"
                type="submit"
                disabled={!candidate || pending}
              >
                {pending ? 'Checking…' : 'Check move'}
              </button>
            </div>
          ) : (
            <p className="form-error">
              No legal moves are available for this position.
            </p>
          )}

          {error ? (
            <p className="form-error" role="alert">
              Could not check that move: {errorMessage(error)}
            </p>
          ) : null}

          <p className="form-hint">
            The server grades your move against the report’s position and engine
            depth. The board and report stay on the played move.
          </p>
        </form>
      )}
    </section>
  )
}

interface TryResultProps {
  result: TryResponse
  candidate: string
  onAnother: () => void
  onReset: () => void
}

function TryResult({
  result,
  candidate,
  onAnother,
  onReset,
}: TryResultProps) {
  const bestLine =
    result.pv_san.length > 0 ? result.pv_san.join(' ') : 'Unavailable.'

  return (
    <div className="try-again-result">
      <p
        className={`try-again-outcome ${
          result.accepted
            ? 'try-again-outcome--accepted'
            : 'try-again-outcome--rejected'
        }`}
        role="status"
      >
        <strong>{result.accepted ? 'Accepted' : 'Rejected'}</strong>
        {result.accepted
          ? ' — this move stays below the inaccuracy threshold.'
          : ` — ${CLASSIFICATION_LABELS[result.classification]} by the report’s thresholds.`}
      </p>

      <p className="try-again-choice">
        You chose <strong>{candidate}</strong>.
      </p>

      <dl className="try-again-facts">
        <div>
          <dt>Win% drop</dt>
          <dd>{result.drop_win_pct.toFixed(1)} win%-points</dd>
        </div>
        <div>
          <dt>Threshold</dt>
          <dd>{result.threshold_win_pct.toFixed(1)} win%-points</dd>
        </div>
        <div>
          <dt>Best move</dt>
          <dd>{result.best_san || 'Unavailable'}</dd>
        </div>
        <div>
          <dt>Best line</dt>
          <dd>{bestLine}</dd>
        </div>
      </dl>

      <p className="try-again-reason">{result.reason}</p>

      <div className="try-again-actions">
        <button
          className="button button--secondary"
          type="button"
          onClick={onAnother}
        >
          Try another move
        </button>
        <button
          className="button button--secondary"
          type="button"
          onClick={onReset}
        >
          Reset
        </button>
      </div>
    </div>
  )
}
