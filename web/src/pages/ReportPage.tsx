import { Link, useParams, useSearchParams } from 'react-router-dom'
import type { Side } from '../api'
import { ErrorState } from '../components/ErrorState'
import { Review } from '../components/Review'
import { playerGamesPath } from '../lib/analysis'

/**
 * A durable report link: `game_key` and `user_color` live in the query string,
 * so the report survives reloads without depending on navigation state.
 */
export function ReportPage() {
  const { username = '' } = useParams()
  const [searchParams] = useSearchParams()
  const gameKey = searchParams.get('game_key')
  const userColor = parseUserColor(searchParams.get('user_color'))
  const gamesPath = playerGamesPath(username)

  return (
    <section className="report-page" aria-labelledby="report-title">
      <header className="page-intro">
        <p className="eyebrow">Saved analysis</p>
        <h1 id="report-title">Review this game.</h1>
        <p className="lede">
          This report is replayed from the frozen analysis stored on the server.
        </p>
        <p className="analysis-context">
          <Link className="text-link" to={gamesPath}>
            Back to games
          </Link>
        </p>
      </header>

      {gameKey && userColor ? (
        <Review
          gameKey={gameKey}
          userColor={userColor}
          gamesPath={gamesPath}
        />
      ) : (
        <ErrorState
          title="This report link is incomplete"
          message="The link needs a game key and either white or black as the colour. Open the player’s games and choose Analyze again."
          action={
            <Link className="button button--secondary" to={gamesPath}>
              Back to games
            </Link>
          }
        />
      )}
    </section>
  )
}

function parseUserColor(value: string | null): Side | null {
  return value === 'white' || value === 'black' ? value : null
}
