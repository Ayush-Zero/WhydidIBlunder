import { useState } from 'react'
import { Link } from 'react-router-dom'
import { errorMessage, isApiError, type Side } from '../api'
import { useReviewData } from '../hooks/useReviewData'
import { sideLabel } from '../lib/games'
import { phaseBreakdown, whitePovEval } from '../lib/review'
import { ErrorState } from './ErrorState'
import { EvalBar } from './EvalBar'
import { LoadingState } from './LoadingState'
import { MistakesPanel } from './MistakesPanel'
import { MoveList } from './MoveList'
import { ReviewBoard } from './ReviewBoard'
import { ReviewSummary } from './ReviewSummary'

interface ReviewProps {
  gameKey: string
  userColor: Side
  gamesPath: string
}

/**
 * The C1 review slice: one report, one board, one grouped move list.
 *
 * The board shows `fen_before` for the selected ply so C3 can draw arrows on
 * the position the mover actually faced. The large eval bar describes that
 * same visible position; each move row shows the evaluation after its move.
 */
export function Review({ gameKey, userColor, gamesPath }: ReviewProps) {
  const { phase, report, positions, error, retry } = useReviewData(
    gameKey,
    userColor,
  )
  const [selectedPly, setSelectedPly] = useState(1)

  if (phase === 'loading') {
    return (
      <section className="review" aria-label="Game review">
        <LoadingState label="Loading the board and moves…" />
      </section>
    )
  }

  if (phase === 'error' || !report || !positions) {
    const copy = describeReviewError(error)
    return (
      <ReviewFailure
        title={copy.title}
        message={copy.message}
        gamesPath={gamesPath}
        onRetry={retry}
      />
    )
  }

  if (report.moves.length === 0) {
    return (
      <section className="review" aria-label="Game review">
        <p className="state-copy">This report has no moves to review.</p>
        <p className="review-footer">
          <Link className="text-link" to={gamesPath}>
            Back to games
          </Link>
        </p>
      </section>
    )
  }

  const firstPly = report.moves[0].ply
  const activePly = report.moves.some((move) => move.ply === selectedPly)
    ? selectedPly
    : firstPly
  const move =
    report.moves.find((candidate) => candidate.ply === activePly) ??
    report.moves[0]
  const position = positions.positions.find(
    (candidate) => candidate.ply === activePly,
  )

  if (!position || positions.positions.length !== report.moves.length) {
    return (
      <ReviewFailure
        title="The board data does not match this report"
        message="The server returned a report and positions that cannot be paired. Try loading the review again."
        gamesPath={gamesPath}
        onRetry={retry}
      />
    )
  }

  const evaluation = whitePovEval(move, 'before')
  const userMoves = report.moves.filter(
    (candidate) => candidate.side === report.summary.user_color,
  )
  const flaggedMoves = userMoves.filter(
    (candidate) => candidate.classification !== 'ok',
  )
  const phaseCounts = phaseBreakdown(userMoves, positions.positions)

  return (
    <section className="review" aria-labelledby="review-title">
      <header className="review-header">
        <div>
          <p className="eyebrow">Game review</p>
          <h2 id="review-title">
            {report.game.white} vs {report.game.black}
          </h2>
        </div>
        <p className="review-result">{report.game.result}</p>
      </header>

      <dl className="review-meta">
        <div>
          <dt>Engine</dt>
          <dd>{report.engine.name}</dd>
        </div>
        <div>
          <dt>Depth</dt>
          <dd>{report.engine.depth}</dd>
        </div>
        <div>
          <dt>MultiPV</dt>
          <dd>{report.engine.multipv}</dd>
        </div>
        <div>
          <dt>Your side</dt>
          <dd>{sideLabel(userColor)}</dd>
        </div>
      </dl>

      <ReviewSummary summary={report.summary} phaseCounts={phaseCounts} />

      <div className="review-grid">
        <div className="review-board-row">
          <EvalBar evaluation={evaluation} />
          <ReviewBoard fen={position.fen_before} orientation={userColor} />
        </div>

        <div className="review-side">
          <p className="review-move-label" role="status">
            {move.move_number}
            {move.side === 'white' ? '.' : '…'} {move.san}
          </p>
          <MoveList
            moves={report.moves}
            selectedPly={activePly}
            onSelect={setSelectedPly}
          />
        </div>
      </div>

      <MistakesPanel
        moves={flaggedMoves}
        summary={report.summary}
        selectedPly={activePly}
        onSelect={setSelectedPly}
      />

      <p className="review-footer">
        <Link className="text-link" to={gamesPath}>
          Back to games
        </Link>
      </p>
    </section>
  )
}

interface ReviewFailureProps {
  title: string
  message: string
  gamesPath: string
  onRetry: () => void
}

function ReviewFailure({
  title,
  message,
  gamesPath,
  onRetry,
}: ReviewFailureProps) {
  return (
    <section className="review" aria-label="Game review">
      <ErrorState
        title={title}
        message={message}
        action={
          <>
            <button
              className="button button--secondary"
              type="button"
              onClick={onRetry}
            >
              Retry
            </button>
            <Link className="button button--secondary" to={gamesPath}>
              Back to games
            </Link>
          </>
        }
      />
    </section>
  )
}

function describeReviewError(error: unknown): {
  title: string
  message: string
} {
  if (!isApiError(error)) {
    return { title: 'Could not load the review', message: errorMessage(error) }
  }

  switch (error.kind) {
    case 'not-found':
      return {
        title: 'This report is no longer available',
        message: error.message,
      }
    case 'conflict':
      return {
        title: 'This report cannot be replayed',
        message: error.message,
      }
    case 'network':
      return {
        title: 'Can’t reach the analysis server',
        message: error.message,
      }
    case 'timeout':
      return { title: 'The server took too long', message: error.message }
    case 'unavailable':
      return {
        title: 'The analysis service is not ready',
        message: error.message,
      }
    case 'validation':
      return {
        title: 'This report cannot be shown',
        message: error.message,
      }
    default:
      return { title: 'Could not load the review', message: error.message }
  }
}
