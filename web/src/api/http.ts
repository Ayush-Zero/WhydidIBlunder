import { ApiError, type ApiErrorKind } from './errors'

const DEFAULT_TIMEOUT_MS = 15_000

export interface RequestConfig {
  /** Caller-owned cancellation, e.g. a component unmounting. */
  signal?: AbortSignal
  /** Override the 15s default; 0 disables the timeout. */
  timeoutMs?: number
}

export interface FetchJsonOptions extends RequestConfig {
  method?: 'GET' | 'POST'
  body?: unknown
  headers?: Record<string, string>
}

/**
 * Fetch and JSON-parse one URL, mapping HTTP and network failures to ApiError.
 *
 * The timeout and the caller's signal share an AbortController, but the thrown
 * kind distinguishes the two: a caller cancellation should not be rendered as
 * an error, while a timeout should.
 */
export async function fetchJson<T>(
  url: string,
  options: FetchJsonOptions = {},
): Promise<T> {
  if (options.signal?.aborted) {
    throw new ApiError('aborted')
  }

  const controller = new AbortController()
  let timedOut = false
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const timer =
    timeoutMs > 0
      ? setTimeout(() => {
          timedOut = true
          controller.abort()
        }, timeoutMs)
      : null

  const unlinkSignal = linkAbortSignal(options.signal, controller)

  try {
    const body = options.body === undefined ? undefined : JSON.stringify(options.body)
    const headers: Record<string, string> = {
      Accept: 'application/json',
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...options.headers,
    }

    let response: Response
    try {
      response = await fetch(url, {
        method: options.method ?? 'GET',
        headers,
        body,
        cache: 'no-store',
        signal: controller.signal,
      })
    } catch (cause) {
      throw fetchFailure(cause, controller.signal, timedOut)
    }

    let text: string
    try {
      text = await response.text()
    } catch (cause) {
      throw fetchFailure(cause, controller.signal, timedOut)
    }

    let payload: unknown = null
    if (text) {
      try {
        payload = JSON.parse(text) as unknown
      } catch (cause) {
        if (!response.ok) {
          throw apiErrorFromResponse(response.status, null, response.headers)
        }
        throw new ApiError('invalid-response', {
          status: response.status,
          cause,
        })
      }
    }

    if (!response.ok) {
      throw apiErrorFromResponse(response.status, payload, response.headers)
    }

    return payload as T
  } finally {
    if (timer !== null) clearTimeout(timer)
    unlinkSignal()
  }
}

function linkAbortSignal(
  signal: AbortSignal | undefined,
  controller: AbortController,
): () => void {
  if (!signal) return () => {}
  if (signal.aborted) {
    controller.abort()
    return () => {}
  }

  const onAbort = () => controller.abort()
  signal.addEventListener('abort', onAbort, { once: true })
  return () => signal.removeEventListener('abort', onAbort)
}

function fetchFailure(
  cause: unknown,
  signal: AbortSignal,
  timedOut: boolean,
): ApiError {
  if (timedOut) return new ApiError('timeout', { cause })
  if (signal.aborted) return new ApiError('aborted', { cause })
  return new ApiError('network', { cause })
}

function apiErrorFromResponse(
  status: number,
  payload: unknown,
  headers: Headers,
): ApiError {
  const kind = kindForStatus(status)
  return new ApiError(kind, {
    status,
    detail: extractDetail(payload),
    retryAfterSeconds:
      kind === 'rate-limited' ? readRetryAfter(headers.get('retry-after')) : null,
  })
}

function kindForStatus(status: number): ApiErrorKind {
  switch (status) {
    case 404:
      return 'not-found'
    case 409:
      return 'conflict'
    case 422:
      return 'validation'
    case 429:
      return 'rate-limited'
    case 502:
      return 'upstream'
    case 503:
      return 'unavailable'
    case 504:
      return 'timeout'
    default:
      return status >= 500 ? 'server' : 'unknown'
  }
}

function extractDetail(payload: unknown): string | null {
  if (!isRecord(payload)) return null
  const detail = payload.detail
  return typeof detail === 'string' ? detail : null
}

function readRetryAfter(raw: string | null): number | null {
  if (!raw) return null
  const seconds = Number(raw)
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
