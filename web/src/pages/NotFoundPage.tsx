import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <section className="not-found" aria-labelledby="not-found-title">
      <p className="eyebrow">404</p>
      <h1 id="not-found-title">That page is not on the board.</h1>
      <p className="lede">
        The link may be stale, or the page may not exist yet.
      </p>
      <Link className="text-link" to="/">
        Return home
      </Link>
    </section>
  )
}
