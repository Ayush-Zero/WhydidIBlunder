import { Chess } from 'chess.js'
import type {
  Classification,
  MoveEvaluation,
  Phase,
  ReviewPosition,
} from '../api'

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
  return formatScore(evaluation.cp, evaluation.mate, evaluation.terminal)
}

function formatScore(cp: number, mate: number | null, terminal: boolean): string {
  if (terminal) return '#'

  if (mate !== null) {
    const distance = Math.abs(mate)
    return `${mate < 0 ? '-' : ''}M${distance}`
  }

  const pawns = cp / 100
  const sign = pawns > 0 ? '+' : ''
  return `${sign}${pawns.toFixed(1)}`
}

/** Format the eval of a reported move from the mover's point of view. */
function formatMoveEval(move: MoveEvaluation, point: EvalPoint): string {
  const before = point === 'before'
  const mate = before ? move.mate_before : move.mate_after
  const terminal = mate === 0 || (!before && isCheckmateSan(move.san))

  return formatScore(
    before ? move.eval_before_cp : move.eval_after_cp,
    mate,
    terminal,
  )
}

/**
 * A factual note for a mistake/blunder that did not cost the game.
 *
 * The engine label is never softened. "Still winning" is reserved for a
 * decisive advantage (>= 70% win chance or a forced mate); a smaller edge is
 * "Still ahead". This is the product decision for the `11.Qg3` case: a
 * 30-point drop that leaves +1.0 is a blunder, but the panel says
 * "Still ahead (+1.0)".
 */
export function stillAheadNote(move: MoveEvaluation): string | null {
  if (move.classification !== 'blunder' && move.classification !== 'mistake') {
    return null
  }

  if (move.mate_after !== null) {
    return move.mate_after > 0
      ? `Still winning (${formatMoveEval(move, 'after')})`
      : null
  }

  const score = formatMoveEval(move, 'after')
  if (move.eval_after_win_pct >= 70) return `Still winning (${score})`
  if (move.eval_after_win_pct > 50) return `Still ahead (${score})`
  return null
}

/** A played or best-move arrow for `react-chessboard`. */
export interface ReviewArrow {
  startSquare: string
  endSquare: string
  color: string
}

export interface PvLine {
  /** Legal SAN moves from the selected position. */
  moves: string[]
  /** `fens[i]` is after `i` PV moves; `fens[0]` is the selected position. */
  fens: string[]
}

export interface MoveOverlay {
  arrows: ReviewArrow[]
  line: PvLine | null
  /** A visible, non-fatal explanation when the report cannot be drawn. */
  error: string | null
}

const PLAYED_ARROW_COLOR = 'rgba(192, 57, 43, 0.85)'
const BEST_ARROW_COLOR = 'rgba(30, 132, 73, 0.85)'

/**
 * Draw the played and best moves on `fen_before`, and replay the best line.
 *
 * The report stores SAN, not squares, so `chess.js` derives the arrow ends.
 * A played best move is drawn once (green) to avoid blending two arrows.
 * Invalid report data becomes a labelled error instead of a thrown exception.
 */
export function moveOverlay(
  fenBefore: string,
  move: MoveEvaluation,
): MoveOverlay {
  const arrows: ReviewArrow[] = []
  let error: string | null = null

  const played = arrowFor(fenBefore, move.san)
  if (played) {
    arrows.push({ ...played, color: PLAYED_ARROW_COLOR })
  } else {
    error = 'The played move could not be shown on this position.'
  }

  const best = arrowFor(fenBefore, move.best_san)
  if (best) {
    const sameAsPlayed =
      played?.startSquare === best.startSquare &&
      played?.endSquare === best.endSquare
    if (!sameAsPlayed) {
      arrows.push({ ...best, color: BEST_ARROW_COLOR })
    }
  } else {
    error = appendProblem(error, 'The best move is unavailable for this position.')
  }

  const line = pvLine(fenBefore, move.pv_san)
  if (!line) {
    error = appendProblem(error, 'The best line is unavailable for this position.')
  }

  return { arrows, line, error }
}

interface ArrowSquares {
  startSquare: string
  endSquare: string
}

function arrowFor(fen: string, san: string): ArrowSquares | null {
  if (!san) return null

  try {
    const move = new Chess(fen).move(san)
    return { startSquare: move.from, endSquare: move.to }
  } catch {
    return null
  }
}

function pvLine(fenBefore: string, moves: string[]): PvLine | null {
  if (!Array.isArray(moves) || moves.length === 0) return null

  try {
    const chess = new Chess(fenBefore)
    const fens = [fenBefore]

    for (const san of moves) {
      chess.move(san)
      fens.push(chess.fen())
    }

    return { moves: [...moves], fens }
  } catch {
    return null
  }
}

function appendProblem(current: string | null, next: string): string {
  return current ? `${current} ${next}` : next
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

export const CLASSIFICATION_LABELS: Record<Classification, string> = {
  ok: 'OK',
  inaccuracy: 'Inaccuracy',
  mistake: 'Mistake',
  blunder: 'Blunder',
}

const TAG_LABELS: Record<string, string> = {
  missed_mate: 'Missed mate',
  allowed_mate: 'Allowed mate',
  hanging_piece: 'Hanging piece',
  missed_capture: 'Missed capture',
  fork: 'Fork',
  pin: 'Pin',
}

/** Human-readable label for an analyzer tag; unknown tags are humanised. */
export function tagLabel(tag: string): string {
  return TAG_LABELS[tag] ?? tag.replaceAll('_', ' ')
}

export interface PhaseCounts {
  phase: Phase
  blunders: number
  mistakes: number
  inaccuracies: number
}

/**
 * Count flagged moves by the phase of the position the mover faced.
 *
 * The phase comes from the positions endpoint, never from the move number:
 * `detect_phase` has one implementation and it is on the server.
 */
export function phaseBreakdown(
  moves: MoveEvaluation[],
  positions: ReviewPosition[],
): PhaseCounts[] {
  const phaseByPly = new Map(
    positions.map((position) => [position.ply, position.phase]),
  )
  const rows: PhaseCounts[] = [
    { phase: 'opening', blunders: 0, mistakes: 0, inaccuracies: 0 },
    { phase: 'middlegame', blunders: 0, mistakes: 0, inaccuracies: 0 },
    { phase: 'endgame', blunders: 0, mistakes: 0, inaccuracies: 0 },
  ]
  const rowByPhase = new Map(rows.map((row) => [row.phase, row]))

  for (const move of moves) {
    if (move.classification === 'ok') continue

    const phase = phaseByPly.get(move.ply)
    const row = phase ? rowByPhase.get(phase) : undefined
    if (!row) continue

    if (move.classification === 'blunder') row.blunders += 1
    else if (move.classification === 'mistake') row.mistakes += 1
    else row.inaccuracies += 1
  }

  return rows
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
