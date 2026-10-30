import { API_BASE_URL } from '../lib/env'

/**
 * Build a same-origin (or configured-API) URL for a transport path.
 *
 * Keep all endpoint paths relative here: the browser must never construct a
 * Chess.com URL.
 */
export function apiUrl(path: string): string {
  const suffix = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${suffix}`
}

/**
 * Build a URL for an origin-level endpoint such as /health.
 *
 * A relative API base (the dev proxy and same-origin production default) stays
 * relative. A split deployment's absolute API base resolves /health against
 * the API's origin, not its /api path.
 */
export function serverUrl(path: string): string {
  const suffix = path.startsWith('/') ? path : `/${path}`
  if (!/^[a-z][a-z\d+.-]*:\/\//i.test(API_BASE_URL)) return suffix

  try {
    return new URL(suffix, new URL(API_BASE_URL).origin).toString()
  } catch {
    return suffix
  }
}

/**
 * Encode an opaque game key for a path segment.
 *
 * The key may be a URL itself, so slashes stay path separators; characters that
 * could change the request (?, #, %, spaces) are encoded. C5/A1 accept a path
 * converter, so the server receives the decoded key unchanged.
 */
export function encodeGameKey(gameKey: string): string {
  return gameKey
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/')
    .replace(/%3A/gi, ':')
}
