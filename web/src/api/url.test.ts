import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.unstubAllEnvs()
  vi.resetModules()
})

describe('apiUrl', () => {
  it('joins the configured base and a relative path', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.com/api/')
    vi.resetModules()
    const { apiUrl } = await import('./url')

    expect(apiUrl('/games')).toBe('https://api.example.com/api/games')
    expect(apiUrl('games')).toBe('https://api.example.com/api/games')
  })

  it('uses the default /api prefix when env is blank', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '')
    vi.resetModules()
    const { apiUrl } = await import('./url')

    expect(apiUrl('/games')).toBe('/api/games')
  })
})
