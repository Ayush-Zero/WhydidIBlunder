import { describe, expect, it } from 'vitest'
import { normalizeApiBaseUrl } from './env'

describe('normalizeApiBaseUrl', () => {
  it('defaults to the same-origin /api prefix', () => {
    expect(normalizeApiBaseUrl(undefined)).toBe('/api')
    expect(normalizeApiBaseUrl('   ')).toBe('/api')
  })

  it('treats the origin root as an empty prefix', () => {
    expect(normalizeApiBaseUrl('/')).toBe('')
  })

  it('strips trailing slashes from a configured base', () => {
    expect(normalizeApiBaseUrl('/api/')).toBe('/api')
    expect(normalizeApiBaseUrl('https://api.example.com/api///')).toBe(
      'https://api.example.com/api',
    )
  })
})
