import { Link, Outlet } from 'react-router-dom'

export function AppShell() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <header className="app-header">
        <Link className="brand" to="/" aria-label="WhydidIBlunder home">
          <span className="brand-mark" aria-hidden="true">
            ♟
          </span>
          <span>WhydidIBlunder</span>
        </Link>
        <span className="app-kicker">Server-side Stockfish</span>
      </header>

      <main id="main" className="app-main" tabIndex={-1}>
        <Outlet />
      </main>

      <footer className="app-footer">
        <p>
          Analysis stays on the server. This browser never talks to Chess.com or
          Stockfish directly.
        </p>
      </footer>
    </div>
  )
}
