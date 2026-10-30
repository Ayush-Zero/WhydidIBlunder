import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  api,
  errorMessage,
  isAbortError,
  isApiError,
  type GameList,
  type GameSummary,
} from '../api'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { LoadingState } from '../components/LoadingState'
import { SearchForm } from '../components/SearchForm'
import { GameCard } from '../components/GameCard'
import {
  analysisPath,
  type AnalysisLocationState,
} from '../lib/analysis'
import {
  DEFAULT_DISCOVERY_FILTERS,
  MAX_GAMES_DEFAULT,
  MONTHS_DEFAULT,
  USERNAME_PATTERN,
  filtersFromSearchParams,
  playerPath,
  type DiscoverySearch,
} from '../lib/games'

type LoadState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; data: GameList }
  | { status: 'error'; error: unknown }

interface ErrorCopy {
  title: string
  message: string
}

export function PlayerPage() {
  const { username: routeUsername = '' } = useParams()
  const username = routeUsername.trim()
  const [searchParams] = useSearchParams()
  const filters = filtersFromSearchParams(searchParams)
  const navigate = useNavigate()
  const location = useLocation()
  const [loadResult, setLoadResult] = useState<{
    requestKey: string
    state: LoadState
  }>({ requestKey: '', state: { status: 'idle' } })
  const [retryToken, setRetryToken] = useState(0)
  const [startingGameKey, setStartingGameKey] = useState<string | null>(null)
  const [startError, setStartError] = useState<unknown | null>(null)
  const [storedReportGame, setStoredReportGame] =
    useState<GameSummary | null>(null)

  const usernameIsValid = USERNAME_PATTERN.test(username)
  const requestKey = [
    username,
    filters.months,
    filters.max_games,
    filters.time_class ?? '',
    filters.include_from_position ? '1' : '0',
    retryToken,
  ].join('|')
  const load: LoadState =
    loadResult.requestKey === requestKey
      ? loadResult.state
      : { status: 'loading' }

  useEffect(() => {
    if (!usernameIsValid) return

    let active = true
    const controller = new AbortController()

    api
      .listGames(
        username,
        {
          months: filters.months,
          max_games: filters.max_games,
          time_class: filters.time_class ?? undefined,
          include_from_position: filters.include_from_position || undefined,
        },
        { signal: controller.signal },
      )
      .then((data) => {
        if (active) {
          setLoadResult({ requestKey, state: { status: 'success', data } })
        }
      })
      .catch((error: unknown) => {
        if (!active || isAbortError(error)) return
        setLoadResult({ requestKey, state: { status: 'error', error } })
      })

    return () => {
      active = false
      controller.abort()
    }
  }, [
    filters.include_from_position,
    filters.max_games,
    filters.months,
    filters.time_class,
    requestKey,
    username,
    usernameIsValid,
  ])

  function handleSearch(search: DiscoverySearch) {
    clearAnalysisNotice()
    const nextPath = playerPath(search)
    if (nextPath === `${location.pathname}${location.search}`) {
      setRetryToken((value) => value + 1)
      return
    }
    navigate(nextPath)
  }

  function handleRetry() {
    setRetryToken((value) => value + 1)
  }

  function resetFilters() {
    clearAnalysisNotice()
    navigate(
      playerPath({
        username,
        ...DEFAULT_DISCOVERY_FILTERS,
      }),
    )
  }

  function clearAnalysisNotice() {
    setStartingGameKey(null)
    setStartError(null)
    setStoredReportGame(null)
  }

  async function handleAnalyze(game: GameSummary) {
    if (startingGameKey !== null) return

    setStartingGameKey(game.game_key)
    setStartError(null)
    setStoredReportGame(null)

    try {
      const response = await api.analyzeGame({
        username,
        game_key: game.game_key,
        months: filters.months,
      })

      if (response.job_id) {
        const state: AnalysisLocationState = {
          game,
          months: filters.months,
          reused: response.reused,
          returnTo: `${location.pathname}${location.search}`,
        }
        navigate(analysisPath(username, response.job_id), { state })
        return
      }

      setStoredReportGame(game)
    } catch (error) {
      if (!isAbortError(error)) setStartError(error)
    } finally {
      setStartingGameKey(null)
    }
  }

  const hasActiveFilters =
    filters.months !== MONTHS_DEFAULT ||
    filters.max_games !== MAX_GAMES_DEFAULT ||
    filters.time_class !== null ||
    filters.include_from_position

  const data = load.status === 'success' ? load.data : null
  const displayUsername = data?.username ?? username
  const formKey = [
    username,
    filters.months,
    filters.max_games,
    filters.time_class ?? '',
    filters.include_from_position ? '1' : '0',
  ].join('|')

  return (
    <section className="discovery-page" aria-labelledby="discovery-title">
      <header className="page-intro">
        <p className="eyebrow">Discovery</p>
        <h1 id="discovery-title">Review a player’s recent games.</h1>
        <p className="lede">
          Search any public Chess.com player. We fetch the games through this
          service, so the browser never calls Chess.com directly.
        </p>
      </header>

      <SearchForm
        key={formKey}
        initialUsername={username}
        initialFilters={filters}
        showFilters
        submitLabel="Update games"
        onSubmit={handleSearch}
      />

      {!usernameIsValid ? (
        <ErrorState
          title="That username is not valid"
          message="Chess.com usernames use 1-50 letters, numbers, underscores or hyphens."
          action={
            <Link className="button button--secondary" to="/">
              Start over
            </Link>
          }
        />
      ) : (
        <section
          className="results"
          aria-labelledby="games-title"
          aria-busy={load.status === 'idle' || load.status === 'loading'}
        >
          <div className="results-header">
            <div>
              <p className="eyebrow">Recent games</p>
              <h2 id="games-title">Games for {displayUsername}</h2>
            </div>
            {data ? (
              <p className="result-count" aria-live="polite">
                <strong>{data.count}</strong>{' '}
                {data.count === 1 ? 'game' : 'games'}
              </p>
            ) : null}
          </div>

          {storedReportGame ? (
            <aside className="inline-notice" role="status">
              <p>
                A stored report already exists for{' '}
                <strong>
                  {storedReportGame.white.username} vs{' '}
                  {storedReportGame.black.username}
                </strong>
                . The move-by-move review screen is not connected yet.
              </p>
              <button
                className="button button--secondary button--small"
                type="button"
                onClick={() => setStoredReportGame(null)}
              >
                Dismiss
              </button>
            </aside>
          ) : null}

          {startError ? (
            <AnalysisStartError
              error={startError}
              onDismiss={() => setStartError(null)}
            />
          ) : null}

          {load.status === 'idle' || load.status === 'loading' ? (
            <LoadingState label={`Loading games for ${username}…`} />
          ) : null}

          {load.status === 'error' ? (
            <GamesError error={load.error} onRetry={handleRetry} />
          ) : null}

          {data && data.games.length === 0 ? (
            <EmptyState
              title="No games in that range"
              description={
                hasActiveFilters
                  ? 'Try more months, a different time class, or reset the filters.'
                  : 'Try another username, or search a wider date range.'
              }
              action={
                hasActiveFilters ? (
                  <button
                    className="button button--secondary"
                    type="button"
                    onClick={resetFilters}
                  >
                    Reset filters
                  </button>
                ) : undefined
              }
            />
          ) : null}

          {data && data.games.length > 0 ? (
            <ul className="game-list" aria-label={`Games for ${displayUsername}`}>
              {data.games.map((game) => (
                <li key={game.game_key}>
                  <GameCard
                    game={game}
                    onAnalyze={handleAnalyze}
                    analyzing={startingGameKey === game.game_key}
                    analysisBusy={startingGameKey !== null}
                  />
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      )}
    </section>
  )
}

interface AnalysisStartErrorProps {
  error: unknown
  onDismiss: () => void
}

function AnalysisStartError({ error, onDismiss }: AnalysisStartErrorProps) {
  const copy = describeAnalysisStartError(error)

  return (
    <ErrorState
      title={copy.title}
      message={copy.message}
      action={
        <button
          className="button button--secondary"
          type="button"
          onClick={onDismiss}
        >
          Dismiss
        </button>
      }
    />
  )
}

function describeAnalysisStartError(error: unknown): ErrorCopy {
  if (!isApiError(error)) {
    return { title: 'Could not start the analysis', message: errorMessage(error) }
  }

  switch (error.kind) {
    case 'not-found':
      return { title: 'Game not found', message: error.message }
    case 'validation':
      return { title: 'This game cannot be analysed', message: error.message }
    case 'rate-limited': {
      const wait = error.retryAfterSeconds
        ? ` Try again in ${formatWait(error.retryAfterSeconds)}.`
        : ' Try again later.'
      return { title: 'Analysis limit reached', message: `${error.message}${wait}` }
    }
    case 'upstream':
      return {
        title: 'Chess.com is not responding',
        message: error.message,
      }
    case 'unavailable':
      return {
        title: 'The analysis service is not ready',
        message: error.message,
      }
    case 'network':
      return {
        title: 'Can’t reach the analysis server',
        message: error.message,
      }
    case 'timeout':
      return { title: 'The server took too long', message: error.message }
    case 'conflict':
      return { title: 'That game cannot be analysed', message: error.message }
    default:
      return { title: 'Could not start the analysis', message: error.message }
  }
}

function formatWait(seconds: number): string {
  if (seconds < 60) {
    return `${Math.max(1, Math.ceil(seconds))} seconds`
  }
  const minutes = Math.ceil(seconds / 60)
  return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`
}

interface GamesErrorProps {
  error: unknown
  onRetry: () => void
}

function GamesError({ error, onRetry }: GamesErrorProps) {
  const copy = describeGamesError(error)
  const canRetry = !(
    isApiError(error) &&
    (error.kind === 'not-found' || error.kind === 'validation')
  )

  return (
    <ErrorState
      title={copy.title}
      message={copy.message}
      action={
        canRetry ? (
          <button
            className="button button--secondary"
            type="button"
            onClick={onRetry}
          >
            Retry
          </button>
        ) : undefined
      }
    />
  )
}

function describeGamesError(error: unknown): ErrorCopy {
  if (!isApiError(error)) {
    return { title: 'Could not load games', message: errorMessage(error) }
  }

  switch (error.kind) {
    case 'not-found':
      return { title: 'Player not found', message: error.message }
    case 'upstream':
      return {
        title: 'Chess.com is not responding',
        message: error.message,
      }
    case 'rate-limited':
      return { title: 'Too many requests', message: error.message }
    case 'unavailable':
      return {
        title: 'The analysis service is not ready',
        message: error.message,
      }
    case 'network':
      return {
        title: "Can’t reach the analysis server",
        message: error.message,
      }
    case 'timeout':
      return { title: 'The server took too long', message: error.message }
    case 'validation':
      return { title: 'That search was not accepted', message: error.message }
    default:
      return { title: 'Could not load games', message: errorMessage(error) }
  }
}
