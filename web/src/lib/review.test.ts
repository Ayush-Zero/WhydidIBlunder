import { describe, expect, it } from 'vitest'
import type { MoveEvaluation, ReviewPosition } from '../api'
import {
  formatEval,
  groupMoves,
  phaseBreakdown,
  stillAheadNote,
  tagLabel,
  whiteBarPercent,
  whitePovEval,
} from './review'

function move(overrides: Partial<MoveEvaluation>): MoveEvaluation {
  return {
    ply: 1,
    move_number: 1,
    side: 'white',
    san: 'e4',
    best_san: 'e4',
    pv_san: [],
    eval_before_cp: 0,
    eval_after_cp: 0,
    eval_before_win_pct: 50,
    eval_after_win_pct: 50,
    mate_before: null,
    mate_after: null,
    played_rank: 1,
    drop_win_pct: 0,
    classification: 'ok',
    tags: [],
    suppressed: true,
    suppress_reason: 'best move',
    ...overrides,
  }
}

describe('whitePovEval', () => {
  it('keeps a White move unchanged', () => {
    const evaluation = whitePovEval(
      move({
        side: 'white',
        eval_before_cp: 32,
        eval_after_cp: 48,
        eval_before_win_pct: 54,
        eval_after_win_pct: 57,
      }),
      'before',
    )

    expect(evaluation).toEqual({
      cp: 32,
      mate: null,
      winPct: 54,
      terminal: false,
    })
  })

  it('mirrors a Black move into White POV', () => {
    const evaluation = whitePovEval(
      move({
        side: 'black',
        eval_before_cp: -34,
        eval_after_cp: 20,
        eval_before_win_pct: 46,
        eval_after_win_pct: 52,
      }),
      'before',
    )

    expect(evaluation.cp).toBe(34)
    expect(evaluation.winPct).toBe(54)
  })

  it('flips mate distance for Black', () => {
    const evaluation = whitePovEval(
      move({ side: 'black', mate_before: 5 }),
      'before',
    )

    expect(evaluation.mate).toBe(-5)
    expect(formatEval(evaluation)).toBe('-M5')
  })

  it('marks the final mating move as terminal', () => {
    const evaluation = whitePovEval(
      move({
        side: 'white',
        san: 'Be7#',
        eval_after_cp: 9990,
        eval_after_win_pct: 100,
        mate_after: 1,
      }),
      'after',
    )

    expect(evaluation.terminal).toBe(true)
    expect(formatEval(evaluation)).toBe('#')
    expect(whiteBarPercent(evaluation)).toBe(100)
  })

  it('treats mate zero as checkmate', () => {
    const evaluation = whitePovEval(
      move({ side: 'black', mate_before: 0, eval_before_win_pct: 0 }),
      'before',
    )

    expect(evaluation.terminal).toBe(true)
    expect(formatEval(evaluation)).toBe('#')
    expect(whiteBarPercent(evaluation)).toBe(100)
  })
})

describe('formatEval', () => {
  it('formats centipawns with a sign', () => {
    expect(formatEval({ cp: 32, mate: null, winPct: 54, terminal: false })).toBe(
      '+0.3',
    )
    expect(
      formatEval({ cp: -128, mate: null, winPct: 32, terminal: false }),
    ).toBe('-1.3')
    expect(formatEval({ cp: 0, mate: null, winPct: 50, terminal: false })).toBe(
      '0.0',
    )
  })

  it('formats mate distance for both sides', () => {
    expect(formatEval({ cp: 0, mate: 5, winPct: 100, terminal: false })).toBe(
      'M5',
    )
    expect(formatEval({ cp: 0, mate: -2, winPct: 0, terminal: false })).toBe(
      '-M2',
    )
  })
})

describe('whiteBarPercent', () => {
  it('clamps a normal evaluation away from the edges', () => {
    expect(
      whiteBarPercent({ cp: 0, mate: null, winPct: 99.9, terminal: false }),
    ).toBe(98)
    expect(
      whiteBarPercent({ cp: 0, mate: null, winPct: 0.1, terminal: false }),
    ).toBe(2)
  })

  it('fills the bar for a forced mate', () => {
    expect(
      whiteBarPercent({ cp: 0, mate: 3, winPct: 100, terminal: false }),
    ).toBe(100)
    expect(
      whiteBarPercent({ cp: 0, mate: -3, winPct: 0, terminal: false }),
    ).toBe(0)
  })
})

describe('groupMoves', () => {
  it('pairs plies by full move number', () => {
    const rows = groupMoves([
      move({ ply: 1, move_number: 1, side: 'white', san: 'e4' }),
      move({ ply: 2, move_number: 1, side: 'black', san: 'e5' }),
      move({ ply: 3, move_number: 2, side: 'white', san: 'Nf3' }),
    ])

    expect(rows).toHaveLength(2)
    expect(rows[0]).toMatchObject({
      moveNumber: 1,
      white: { san: 'e4' },
      black: { san: 'e5' },
    })
    expect(rows[1]).toMatchObject({
      moveNumber: 2,
      white: { san: 'Nf3' },
      black: null,
    })
  })
})

describe('phaseBreakdown', () => {
  it('counts flagged moves by the position phase, not the move number', () => {
    const moves = [
      move({ ply: 1, move_number: 1, classification: 'blunder' }),
      move({ ply: 2, move_number: 1, classification: 'mistake' }),
      move({ ply: 3, move_number: 2, classification: 'inaccuracy' }),
      move({ ply: 4, move_number: 2, classification: 'ok' }),
    ]
    const positions: ReviewPosition[] = [
      { ply: 1, fen_before: '', fen_after: '', phase: 'endgame' },
      { ply: 2, fen_before: '', fen_after: '', phase: 'endgame' },
      { ply: 3, fen_before: '', fen_after: '', phase: 'opening' },
      { ply: 4, fen_before: '', fen_after: '', phase: 'opening' },
    ]

    expect(phaseBreakdown(moves, positions)).toEqual([
      { phase: 'opening', blunders: 0, mistakes: 0, inaccuracies: 1 },
      { phase: 'middlegame', blunders: 0, mistakes: 0, inaccuracies: 0 },
      { phase: 'endgame', blunders: 1, mistakes: 1, inaccuracies: 0 },
    ])
  })

  it('ignores moves with no matching position', () => {
    const rows = phaseBreakdown(
      [move({ ply: 9, classification: 'blunder' })],
      [],
    )

    expect(rows.every((row) => row.blunders === 0)).toBe(true)
  })
})

describe('stillAheadNote', () => {
  it('calls a decisive post-move advantage winning', () => {
    expect(
      stillAheadNote(
        move({
          classification: 'blunder',
          eval_after_cp: 300,
          eval_after_win_pct: 80,
        }),
      ),
    ).toBe('Still winning (+3.0)')
  })

  it('uses "still ahead" for the 11.Qg3 +1.0 case', () => {
    expect(
      stillAheadNote(
        move({
          classification: 'blunder',
          eval_after_cp: 100,
          eval_after_win_pct: 60,
        }),
      ),
    ).toBe('Still ahead (+1.0)')
  })

  it('does not soften a move that leaves the player behind', () => {
    expect(
      stillAheadNote(
        move({
          classification: 'blunder',
          eval_after_cp: -200,
          eval_after_win_pct: 30,
        }),
      ),
    ).toBeNull()
  })

  it('only annotates mistakes and blunders', () => {
    const evaluation = {
      eval_after_cp: 300,
      eval_after_win_pct: 80,
    }

    expect(
      stillAheadNote(move({ classification: 'inaccuracy', ...evaluation })),
    ).toBeNull()
    expect(
      stillAheadNote(move({ classification: 'ok', ...evaluation })),
    ).toBeNull()
  })

  it('recognises a forced mate and ignores being mated', () => {
    expect(
      stillAheadNote(
        move({
          classification: 'mistake',
          mate_after: 3,
          eval_after_win_pct: 100,
        }),
      ),
    ).toBe('Still winning (M3)')
    expect(
      stillAheadNote(
        move({
          classification: 'blunder',
          mate_after: -3,
          eval_after_win_pct: 0,
        }),
      ),
    ).toBeNull()
  })
})

describe('tagLabel', () => {
  it('turns report tags into readable labels', () => {
    expect(tagLabel('missed_capture')).toBe('Missed capture')
    expect(tagLabel('fork')).toBe('Fork')
    expect(tagLabel('new_tag')).toBe('new tag')
  })
})
