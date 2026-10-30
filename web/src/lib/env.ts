const DEFAULT_API_BASE_URL = '/api'

/**
 * Normalise VITE_API_BASE_URL so callers can concatenate a path safely.
 *
 * - unset/blank -> /api (the dev proxy and same-origin production default)
 * - "/" -> "" (caller is mounted at the origin root)
 * - trailing slashes removed, so "/api/" and "/api" are one value
 */
export function normalizeApiBaseUrl(value: string | undefined): string {
  const trimmed = value?.trim()
  if (!trimmed) return DEFAULT_API_BASE_URL
  if (trimmed === '/') return ''
  return trimmed.replace(/\/+$/, '')
}

export const API_BASE_URL = normalizeApiBaseUrl(
  import.meta.env.VITE_API_BASE_URL,
)
