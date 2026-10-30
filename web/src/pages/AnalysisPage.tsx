import { Link, useLocation, useParams } from 'react-router-dom'
import {
  errorMessage,
  isApiError,
  type GameSummary,
  type JobStatus,
} from '../api'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import {
  useAnalysisJob,
  type AnalysisPhase,
} from '../hooks/useAnalysisJob'
import {
  playerGamesPath,
  type AnalysisLocationState,
} from '../lib/analysis'
import { sideLabel } from '../lib/games'

interface ErrorCopy {
  title: string
  message: string
}

export function AnalysisPage() {
  const { username = '', jobId = '' } = useParams()
  const location = useLocation()
  const navigation = readAnalysisState(location.state)
  const analysis = useAnalysisJob(jobId)
  const { phase, job, error, cancelling, cancelError, retry, cancel } = analysis
  const game = navigation.game ?? null
  const gamesPath = navigation.returnTo ?? playerGamesPath(username)
  const active = phase === 'queued' || phase === 'running'

  return (
    <section className="analysis-page" aria-labelledby="analysis-title">
      <header className="page-intro">
        <p className="eyebrow">Analysis</p>
        <h1 id="analysis-title">{headingFor(phase)}</h1>
        <p className="lede">
          {game
            ? `${game.white.username} vs ${game.black.username}`
            : 'The server is evaluating the game stored with this job.'}
        </p>
        <div className="analysis-context">
          {game ? (
            <p>
              You played {sideLabel(game.user_color)}.{' '}
            </p>
          ) : null}
          <Link className="text-link" to={gamesPath}>
            Back to games
          </Link>
        </div>
      </header>

      {navigation.reused && active ? (
        <p className="inline-notice" role="status">
          Resuming an existing analysis for this game.
        </p>
      ) : null}

      <div aria-busy={active || phase === 'loading'}>
        {phase === 'loading' ? (
          <LoadingState label="Checking the analysis…" />
        ) : null}

        {phase === 'error' ? (
          <AnalysisError
            error={error}
            job={job}
            gamesPath={gamesPath}
            onRetry={retry}
          />
        ) : null}

        {job && (active || phase === 'done' || phase === 'failed' || phase === 'cancelled') ? (
          <section
            className={`analysis-card analysis-card--${phase}`}
            aria-labelledby="analysis-status-title"
          >
            <p className="analysis-kicker">{statusKicker(phase, cancelling)}</p>
            <h2 id="analysis-status-title">
              {statusHeading(phase, job, cancelling)}
            </h2>
            <p className="state-copy" role="status">
              {statusCopy(phase, job, cancelling)}
            </p>

            {active ? (
              <div
                className="analysis-progress"
                role="progressbar"
                aria-label="Analysis progress"
                aria-valuetext={
                  job.progress_ply > 0
                    ? `Analysing ply ${job.progress_ply}`
                    : 'Waiting for a worker'
                }
              >
                <span className="analysis-progress-bar" aria-hidden="true" />
              </div>
            ) : null}

            <dl className="analysis-facts">
              <div>
                <dt>{active ? 'Plies saved' : 'Plies preserved'}</dt>
                <dd>{job.moves.length}</dd>
              </div>
              <div>
                <dt>{active ? 'Current ply' : 'Last ply'}</dt>
                <dd>{job.progress_ply}</dd>
              </div>
              <div>
                <dt>Your side</dt>
                <dd>{sideLabel(job.user_color)}</dd>
              </div>
            </dl>

            {cancelError ? (
              <p className="form-error" role="alert">
                Could not cancel the analysis: {errorMessage(cancelError)}
              </p>
            ) : null}

            <div className="analysis-actions">
              {active ? (
                <button
                  className="button button--secondary"
                  type="button"
                  disabled={cancelling}
                  onClick={cancel}
                >
                  {cancelling ? 'Cancelling…' : 'Cancel analysis'}
                </button>
              ) : (
                <Link className="button button--secondary" to={gamesPath}>
                  Back to games
                </Link>
              )}
            </div>
          </section>
        ) : null}
      </div>
    </section>
  )
}

function headingFor(phase: string): string {
  switch (phase) {
    case 'done':
      return 'Your game is ready to review.'
    case 'failed':
      return 'The analysis could not finish.'
    case 'cancelled':
      return 'The analysis was cancelled.'
    case 'error':
      return 'We lost track of this analysis.'
    default:
      return 'Working through your game.'
  }
}

function statusKicker(phase: AnalysisPhase, cancelling: boolean): string {
  if (cancelling) return 'Cancelling'
  switch (phase) {
    case 'queued':
      return 'Queued'
    case 'running':
      return 'Running'
    case 'done':
      return 'Complete'
    case 'failed':
      return 'Failed'
    case 'cancelled':
      return 'Cancelled'
    default:
      return 'Status unavailable'
  }
}

function statusHeading(
  phase: AnalysisPhase,
  job: JobStatus,
  cancelling: boolean,
): string {
  if (cancelling) return 'Stopping the analysis.'
  switch (phase) {
    case 'queued':
      return 'Waiting for a worker.'
    case 'running':
      return job.progress_ply > 0
        ? `Analysing ply ${job.progress_ply}.`
        : 'Starting the engine.'
    case 'done':
      return 'Analysis complete.'
    case 'failed':
      return 'The worker stopped before finishing.'
    case 'cancelled':
      return 'No further plies will be evaluated.'
    default:
      return 'Analysis status unavailable.'
  }
}

function statusCopy(
  phase: AnalysisPhase,
  job: JobStatus,
  cancelling: boolean,
): string {
  if (cancelling) return 'Waiting for the server to confirm the cancellation.'

  switch (phase) {
    case 'queued':
      return 'The job is queued. The page refreshes automatically when it starts.'
    case 'running':
      return job.moves.length > 0
        ? `${countLabel(job.moves.length)} saved so far. The page refreshes automatically.`
        : 'The engine is warming up. The page refreshes automatically.'
    case 'done':
      return `${countLabel(job.moves.length)} evaluated and saved on the server.`
    case 'failed':
      return job.error
        ? `${job.error} ${preservedCopy(job.moves.length)}`
        : `The worker reported a failure. ${preservedCopy(job.moves.length)}`
    case 'cancelled':
      return preservedCopy(job.moves.length)
    default:
      return 'The job is no longer reporting a status.'
  }
}

function preservedCopy(count: number): string {
  return count === 0
    ? 'No plies were saved before it stopped.'
    : `${countLabel(count)} saved before it stopped are preserved.`
}

function countLabel(count: number): string {
  return `${count} ${count === 1 ? 'ply' : 'plies'}`
}

interface AnalysisErrorProps {
  error: unknown
  job: JobStatus | null
  gamesPath: string
  onRetry: () => void
}

function AnalysisError({ error, job, gamesPath, onRetry }: AnalysisErrorProps) {
  const copy = describeAnalysisError(error)
  const canRetry = !(
    isApiError(error) &&
    (error.kind === 'not-found' || error.kind === 'validation')
  )
  const lastKnown =
    job && job.moves.length > 0
      ? ` Last known progress: ${countLabel(job.moves.length)} saved.`
      : ''

  return (
    <ErrorState
      title={copy.title}
      message={`${copy.message}${lastKnown}`}
      action={
        <>
          {canRetry ? (
            <button
              className="button button--secondary"
              type="button"
              onClick={onRetry}
            >
              Retry
            </button>
          ) : null}
          <Link className="button button--secondary" to={gamesPath}>
            Back to games
          </Link>
        </>
      }
    />
  )
}

function describeAnalysisError(error: unknown): ErrorCopy {
  if (!isApiError(error)) {
    return { title: 'Could not check the analysis', message: errorMessage(error) }
  }

  switch (error.kind) {
    case 'not-found':
      return {
        title: 'This analysis is no longer available',
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
    case 'rate-limited':
      return { title: 'Too many requests', message: error.message }
    case 'validation':
      return { title: 'That analysis cannot be shown', message: error.message }
    default:
      return { title: 'Could not check the analysis', message: error.message }
  }
}

function readAnalysisState(value: unknown): AnalysisLocationState {
  if (typeof value !== 'object' || value === null) return {}

  const record = value as Record<string, unknown>
  return {
    game: isGameSummary(record.game) ? record.game : undefined,
    reused: record.reused === true,
    months: typeof record.months === 'number' ? record.months : undefined,
    returnTo: safeReturnTo(record.returnTo),
  }
}

function safeReturnTo(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  return value.startsWith('/player/') ? value : undefined
}

function isGameSummary(value: unknown): value is GameSummary {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return typeof record.game_key === 'string'
}
