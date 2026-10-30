import { useId, type ReactNode } from 'react'
import type { GameSummary, PlayerSummary } from '../api'
import {
  formatGameDate,
  formatTimeControl,
  gameDateIso,
  outcomeLabel,
  playerResultLabel,
  safeChessComUrl,
  sideLabel,
  timeClassLabel,
  userOutcome,
} from '../lib/games'

interface GameCardProps {
  game: GameSummary
  onAnalyze?: (game: GameSummary) => void
  analyzing?: boolean
  analysisBusy?: boolean
}

export function GameCard({
  game,
  onAnalyze,
  analyzing = false,
  analysisBusy = false,
}: GameCardProps) {
  const titleId = useId()
  const noteId = useId()
  const outcome = userOutcome(game)
  const timeControl = formatTimeControl(game.time_control)
  const safeUrl = safeChessComUrl(game.url)
  const blockedReason = game.from_position
    ? 'Custom-start games cannot be analysed: they have no ply 1.'
    : game.user_color === null
      ? 'The requested player’s colour could not be determined, so this game cannot be analysed.'
      : null

  return (
    <article className="game-card" aria-labelledby={titleId}>
      <header className="game-card-header">
        <div>
          <p className="game-card-kicker">
            {timeClassLabel(game.time_class)} · {game.rated ? 'Rated' : 'Casual'}
          </p>
          <h3 id={titleId} className="game-card-title">
            {game.white.username} vs {game.black.username}
          </h3>
        </div>
        <span className={`result-pill result-pill--${outcome}`}>
          {outcomeLabel(outcome)}
        </span>
      </header>

      <div className="game-players">
        <PlayerRow
          side="white"
          player={game.white}
          isUser={game.user_color === 'white'}
        />
        <PlayerRow
          side="black"
          player={game.black}
          isUser={game.user_color === 'black'}
        />
      </div>

      <dl className="game-meta">
        <Meta label="Result" value={game.result ?? 'Unknown'} />
        <Meta
          label="Time"
          value={[timeClassLabel(game.time_class), timeControl]
            .filter(Boolean)
            .join(' · ')}
        />
        <Meta
          label="Ended"
          value={
            <time dateTime={gameDateIso(game.end_time)}>
              {formatGameDate(game.end_time)}
            </time>
          }
        />
      </dl>

      {game.is_bot || game.from_position ? (
        <div className="game-badges" aria-label="Game flags">
          {game.is_bot ? (
            <span
              className="badge badge--warning"
              title="Bot game: the rating is not comparable to human games"
            >
              Bot
            </span>
          ) : null}
          {game.from_position ? (
            <span className="badge badge--danger">Custom start</span>
          ) : null}
        </div>
      ) : null}

      <footer className="game-card-footer">
        {safeUrl ? (
          <a
            className="text-link"
            href={safeUrl}
            target="_blank"
            rel="noreferrer noopener"
          >
            Open game on Chess.com
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        ) : null}

        <div className="game-analysis">
          {blockedReason ? (
            <p id={noteId} className="game-note">
              {blockedReason}
            </p>
          ) : null}
          <button
            className="button button--secondary button--small"
            type="button"
            disabled={
              blockedReason !== null || onAnalyze === undefined || analysisBusy
            }
            aria-busy={analyzing || undefined}
            aria-describedby={blockedReason ? noteId : undefined}
            onClick={() => onAnalyze?.(game)}
          >
            {analyzing ? 'Starting…' : 'Analyze'}
          </button>
        </div>
      </footer>
    </article>
  )
}

interface PlayerRowProps {
  side: 'white' | 'black'
  player: PlayerSummary
  isUser: boolean
}

function PlayerRow({ side, player, isUser }: PlayerRowProps) {
  return (
    <div className={`game-player${isUser ? ' game-player--user' : ''}`}>
      <span className="game-player-side">{sideLabel(side)}</span>
      <span className="game-player-name">{player.username}</span>
      {player.title ? <span className="game-player-title">{player.title}</span> : null}
      <span className="game-player-rating">
        {player.rating === null ? 'Unrated' : player.rating}
      </span>
      <span className="game-player-result">
        {playerResultLabel(player.result)}
      </span>
      {isUser ? <span className="you-badge">You</span> : null}
    </div>
  )
}

interface MetaProps {
  label: string
  value: ReactNode
}

function Meta({ label, value }: MetaProps) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  )
}
