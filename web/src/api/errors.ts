export type ApiErrorKind =
  | 'not-found'
  | 'validation'
  | 'rate-limited'
  | 'conflict'
  | 'upstream'
  | 'unavailable'
  | 'network'
  | 'timeout'
  | 'aborted'
  | 'server'
  | 'invalid-response'
  | 'unknown'

const FALLBACK_MESSAGES: Record<ApiErrorKind, string> = {
  'not-found': 'We could not find that item.',
  validation: 'The request was not valid. Check the details and try again.',
  'rate-limited': 'You have reached the limit for now. Try again later.',
  conflict: 'That stored game cannot be shown. Try another game.',
  upstream: 'Chess.com is not responding right now. Try again in a moment.',
  unavailable:
    'The analysis service is not ready yet. Try again shortly.',
  network:
    "Can't reach the analysis server. Check your connection and try again.",
  timeout: 'The server took too long to respond. Try again.',
  aborted: 'The request was cancelled.',
  server: 'The server returned an error. Try again in a moment.',
  'invalid-response':
    'The server returned an unexpected response. Try again in a moment.',
  unknown: 'Something went wrong. Try again.',
}

/** Kinds where the server's one-line detail is already user-facing. */
const DETAIL_IS_USEFUL: ReadonlySet<ApiErrorKind> = new Set([
  'not-found',
  'validation',
  'rate-limited',
])

export interface ApiErrorOptions {
  status?: number | null
  detail?: string | null
  retryAfterSeconds?: number | null
  cause?: unknown
}

/**
 * A transport/API failure with a stable kind and a friendly message.
 *
 * `detail` is the server's `{"detail": "..."}` when it was a string; it is kept
 * for debugging and for screens that can add context. `message` is safe to show.
 */
export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  readonly detail: string | null
  readonly retryAfterSeconds: number | null

  constructor(kind: ApiErrorKind, options: ApiErrorOptions = {}) {
    const detail = options.detail?.trim() || null
    const message =
      detail && DETAIL_IS_USEFUL.has(kind) ? detail : FALLBACK_MESSAGES[kind]

    super(message, options.cause === undefined ? undefined : { cause: options.cause })
    this.name = 'ApiError'
    this.kind = kind
    this.status = options.status ?? null
    this.detail = detail
    this.retryAfterSeconds = options.retryAfterSeconds ?? null
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

export function isAbortError(error: unknown): boolean {
  return error instanceof ApiError && error.kind === 'aborted'
}

/** A safe string for unknown catch values. */
export function errorMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : 'Something went wrong. Try again.'
}
