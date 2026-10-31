import { useState } from 'react'
import { Link } from 'react-router-dom'
import { errorMessage, isApiError, type Side } from '../api'
import { useReviewData } from '../hooks/useReviewData'
import { sideLabel } from '../lib/games'
import { moveOverlay, phaseBreakdown, whitePovEval } from '../lib/review'
import { BestLine } from './BestLine'
import { ErrorState } from './ErrorState'
import { EvalBar } from './EvalBar'
import { LoadingState } from './LoadingState'
import { ReviewBoard } from './ReviewBoard'
import { ReviewNav } from './ReviewNav'
import { ReviewPanels } from './ReviewPanels'
import { ReviewSummary } from './ReviewSummary'

interface ReviewProps {
  gameKey: string
  userColor: Side
  gamesPath: string
}

/**
 * The review screen: one report, one board, one grouped move list.
 *
 * The board shows `fen_before` for the selected ply, with played/best arrows
 * drawn on the position the mover actually faced. The large eval bar describes
 * the selected position; each move row shows the evaluation after its move.
 * Clicking a best-line move previews that PV position without changing the
 * report selection.
 */
export function Review({ gameKey, userColor, gamesPath }: ReviewProps) {
  const { phase, report, positions, error, retry } = useReviewData(
    gameKey,
    userColor,
  )
  const [selectedPly, setSelectedPly] = useState(1)
  const [pvIndex, setPvIndex] = useState(0)

  function selectPly(ply: number) {
    setSelectedPly(ply)
    setPvIndex(0)
  }

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
  const overlay = moveOverlay(position.fen_before, move)
  const previewIndex = Math.min(
    pvIndex,
    overlay.line?.moves.length ?? 0,
  )
  const displayedFen =
    previewIndex > 0 && overlay.line
      ? (overlay.line.fens[previewIndex] ?? position.fen_before)
      : position.fen_before
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

      <div className="review-grid">
        <div className="review-board-column">
          <div className="review-board-row">
            <EvalBar evaluation={evaluation} />
            <ReviewBoard
              fen={displayedFen}
              orientation={userColor}
              arrows={previewIndex === 0 ? overlay.arrows : []}
            />
          </div>
          <BestLine
            line={overlay.line}
            error={overlay.error}
            activeIndex={previewIndex}
            onSelect={setPvIndex}
            onReset={() => setPvIndex(0)}
          />
        </div>

        <div className="review-side">
          <p className="review-move-label" role="status">
            {move.move_number}
            {move.side === 'white' ? '.' : '…'} {move.san}
          </p>
          <ReviewNav
            moves={report.moves}
            selectedPly={activePly}
            onSelect={selectPly}
          />
          <ReviewPanels
            moves={report.moves}
            flaggedMoves={flaggedMoves}
            summary={report.summary}
            selectedPly={activePly}
            onSelect={selectPly}
          />
        </div>
      </div>

      <ReviewSummary summary={report.summary} phaseCounts={phaseCounts} />

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
