import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

/**
 * react-chessboard renders a full SVG board and is disproportionate in jsdom.
 * Unit tests assert the wrapper's FEN contract instead; D4's Playwright run
 * exercises the real board.
 */
vi.mock('react-chessboard', async () => {
  const { createElement } = await import('react')
  return {
    Chessboard: ({
      options,
    }: {
      options?: {
        position?: string
        boardOrientation?: string
        arrows?: unknown
      }
    }) =>
      createElement('div', {
        'data-testid': 'mock-chessboard',
        'data-position': options?.position,
        'data-orientation': options?.boardOrientation,
        'data-arrows': JSON.stringify(options?.arrows ?? []),
      }),
  }
})

afterEach(cleanup)
