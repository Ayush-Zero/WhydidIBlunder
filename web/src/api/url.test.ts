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

describe('serverUrl', () => {
  it('keeps a relative health URL same-origin', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '/api')
    vi.resetModules()
    const { serverUrl } = await import('./url')

    expect(serverUrl('/health')).toBe('/health')
    expect(serverUrl('health')).toBe('/health')
  })

  it('resolves health against an absolute API origin, not its path', async () => {
    vi.stubEnv('VITE_API_BASE_URL', 'https://api.example.com/api/')
    vi.resetModules()
    const { serverUrl } = await import('./url')

    expect(serverUrl('/health')).toBe('https://api.example.com/health')
  })
})

describe('encodeGameKey', () => {
  it('preserves URL separators but escapes query and fragment characters', async () => {
    vi.stubEnv('VITE_API_BASE_URL', '/api')
    vi.resetModules()
    const { encodeGameKey } = await import('./url')

    expect(
      encodeGameKey(
        'chesscom-url:https://www.chess.com/game/daily/123?x=1#frag',
      ),
    ).toBe(
      'chesscom-url:https://www.chess.com/game/daily/123%3Fx%3D1%23frag',
    )
  })
})
