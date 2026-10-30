import { setupServer } from 'msw/node'
import { defaultHandlers } from './handlers'

const server = setupServer(...defaultHandlers)

let interceptedFetch: typeof fetch | null = null

/**
 * MSW intercepts Node's absolute fetch URLs; the browser normally resolves
 * `/api/...` itself. Resolve relative URLs against a fixed local origin only
 * for tests, after MSW has installed its interceptor.
 */
function withLocalOrigin(input: RequestInfo | URL): RequestInfo | URL {
  if (typeof input === 'string' && input.startsWith('/')) {
    return new URL(input, 'http://localhost')
  }
  return input
}

export function startMockServer() {
  server.listen({ onUnhandledFrame: 'error' })
  interceptedFetch = globalThis.fetch
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    interceptedFetch?.(withLocalOrigin(input), init) ??
    Promise.reject(new Error('mock server is not running'))) as typeof fetch
}

export function stopMockServer() {
  if (interceptedFetch) {
    globalThis.fetch = interceptedFetch
    interceptedFetch = null
  }
  server.close()
}

export { server }
