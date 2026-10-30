import { API_BASE_URL } from '../lib/env'

/**
 * Build a same-origin (or configured-API) URL for a transport path.
 *
 * A3 will put the typed fetch wrapper on top of this. Keep all endpoint paths
 * relative here: the browser must never construct a Chess.com URL.
 */
export function apiUrl(path: string): string {
  const suffix = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE_URL}${suffix}`
}
