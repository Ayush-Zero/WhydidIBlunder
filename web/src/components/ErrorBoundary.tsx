import { Component, type ErrorInfo, type ReactNode } from 'react'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  hasError: boolean
}

/**
 * Render errors are not API errors: a thrown component used to leave a blank
 * page. Keep the shell and offer a route back instead.
 */
export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled UI error', error, info.componentStack)
  }

  reset = () => {
    this.setState({ hasError: false })
  }

  render() {
    if (!this.state.hasError) return this.props.children

    return (
      <main className="app-main boundary-fallback">
        <section className="state-card state-card--error" role="alert">
          <p className="eyebrow">Something broke</p>
          <h1>We could not draw that screen.</h1>
          <p>
            The rest of the app is still here. Try again, or reload the page if
            the problem persists.
          </p>
          <div className="state-actions">
            <button className="button" type="button" onClick={this.reset}>
              Try again
            </button>
            <button
              className="button button--secondary"
              type="button"
              onClick={() => window.location.reload()}
            >
              Reload page
            </button>
          </div>
        </section>
      </main>
    )
  }
}
