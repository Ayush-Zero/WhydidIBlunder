export function HomePage() {
  return (
    <section className="home-page" aria-labelledby="home-title">
      <p className="eyebrow">Game review</p>
      <h1 id="home-title">Find the move that changed the game.</h1>
      <p className="lede">
        Review Chess.com games with server-side Stockfish. The browser stays a
        thin client: no engine downloads and no direct Chess.com calls.
      </p>

      <aside className="next-step" aria-labelledby="next-step-title">
        <p className="next-step-label">Phase 3 · scaffold</p>
        <h2 id="next-step-title">
          Player search and game discovery are next.
        </h2>
        <p>
          The routed shell, dev proxy and production build are ready. The
          analysis flow will land here.
        </p>
      </aside>
    </section>
  )
}
