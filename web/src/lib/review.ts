import type { MoveEvaluation } from '../api'

/** A position evaluation converted to a consistent White point of view. */
export interface WhiteEval {
  /** Centipawns, positive means White is better. */
  cp: number
  /** Mate distance in moves, positive means White is mating. */
  mate: number | null
  /** White's win chance, 0–100. */
  winPct: number
  /** The evaluated position is checkmate, the terminal `#` case. */
  terminal: boolean
}

export type EvalPoint = 'before' | 'after'

/**
 * Convert one move's mover-POV evaluation to White's POV.
 *
 * The report normalises every score to the mover (`side`). The review always
 * shows White's perspective, so Black's plies are negated (and win% mirrored).
 */
export function whitePovEval(
  move: MoveEvaluation,
  point: EvalPoint,
): WhiteEval {
  const before = point === 'before'
  const rawCp = before ? move.eval_before_cp : move.eval_after_cp
  const rawMate = before ? move.mate_before : move.mate_after
  const rawWinPct = before
    ? move.eval_before_win_pct
    : move.eval_after_win_pct
  const white = move.side === 'white'

  return {
    cp: white ? rawCp : -rawCp,
    mate: rawMate === null ? null : (white ? rawMate : -rawMate),
    winPct: white ? rawWinPct : 100 - rawWinPct,
    terminal: rawMate === 0 || (!before && isCheckmateSan(move.san)),
  }
}

/** Human-readable White-POV eval: `+0.3`, `-1.2`, `M5`, `-M5`, or `#`. */
export function formatEval(evaluation: WhiteEval): string {
  if (evaluation.terminal) return '#'

  if (evaluation.mate !== null) {
    const distance = Math.abs(evaluation.mate)
    return `${evaluation.mate < 0 ? '-' : ''}M${distance}`
  }

  const pawns = evaluation.cp / 100
  const sign = pawns > 0 ? '+' : ''
  return `${sign}${pawns.toFixed(1)}`
}

/**
 * White's share of the eval bar, 0–100.
 *
 * A decided game fills the bar; otherwise the share is clamped so a small
 * advantage never disappears into a zero-width sliver.
 */
export function whiteBarPercent(evaluation: WhiteEval): number {
  if (evaluation.terminal) return evaluation.winPct >= 50 ? 100 : 0
  if (evaluation.mate !== null) return evaluation.mate > 0 ? 100 : 0
  return Math.min(98, Math.max(2, evaluation.winPct))
}

export interface MoveRow {
  moveNumber: number
  white: MoveEvaluation | null
  black: MoveEvaluation | null
}

/** Pair a report's plies into one row per full move. */
export function groupMoves(moves: MoveEvaluation[]): MoveRow[] {
  const rows: MoveRow[] = []

  for (const move of moves) {
    let row = rows.at(-1)
    if (!row || row.moveNumber !== move.move_number) {
      row = { moveNumber: move.move_number, white: null, black: null }
      rows.push(row)
    }

    if (move.side === 'white') {
      row.white = move
    } else {
      row.black = move
    }
  }

  return rows
}

function isCheckmateSan(san: string): boolean {
  return san.endsWith('#')
}
