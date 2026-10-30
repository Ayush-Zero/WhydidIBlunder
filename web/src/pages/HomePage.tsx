import { useNavigate } from 'react-router-dom'
import { SearchForm } from '../components/SearchForm'
import { playerPath, type DiscoverySearch } from '../lib/games'

export function HomePage() {
  const navigate = useNavigate()

  function handleSearch(search: DiscoverySearch) {
    navigate(playerPath(search))
  }

  return (
    <section className="home-page" aria-labelledby="home-title">
      <p className="eyebrow">Game review</p>
      <h1 id="home-title">Find the move that changed the game.</h1>
      <p className="lede">
        Review Chess.com games with server-side Stockfish. The browser stays a
        thin client: no engine downloads and no direct Chess.com calls.
      </p>

      <SearchForm onSubmit={handleSearch} submitLabel="Find games" autoFocus />

      <p className="home-note">
        No Chess.com login. We only read public games, then the server analyses
        one of them at a time.
      </p>
    </section>
  )
}
