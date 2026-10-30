/** Wire types for the FastAPI contract. These mirror docs/api.md. */

export type Side = 'white' | 'black'
export type Phase = 'opening' | 'middlegame' | 'endgame'
export type Classification = 'ok' | 'inaccuracy' | 'mistake' | 'blunder'
export type TimeClass = 'bullet' | 'blitz' | 'rapid' | 'daily'

/** C3/C4 job lifecycle. C2 only returns queued/running/done. */
export type JobStatusValue =
  | 'queued'
  | 'running'
  | 'done'
  | 'failed'
  | 'cancelled'

export type AnalyzeStatus = 'queued' | 'running' | 'done'

// --- C1: player games ------------------------------------------------------

export interface PlayerSummary {
  username: string
  rating: number | null
  result: string | null
  title: string | null
}

export interface GameSummary {
  /** Opaque. May contain ':' and '/'; never parse it. */
  game_key: string
  url: string | null
  white: PlayerSummary
  black: PlayerSummary
  result: string | null
  time_class: string | null
  time_control: string | null
  rated: boolean
  rules: string | null
  end_time: number | null
  is_bot: boolean
  from_position: boolean
  /** The requested player's side, or null only for inconsistent upstream data. */
  user_color: Side | null
}

export interface GameList {
  username: string
  count: number
  games: GameSummary[]
}

export interface GameListQuery {
  months?: number
  max_games?: number
  time_class?: TimeClass
  include_bots?: boolean
  include_from_position?: boolean
}

// --- C2: request an analysis ----------------------------------------------

export interface AnalyzeRequest {
  username: string
  game_key: string
  months?: number
}

export interface AnalyzeResponse {
  /** Null only when a stored report outlived its job row. */
  job_id: string | null
  status: AnalyzeStatus
  reused: boolean
  game_key: string
  user_color: Side
}

// --- C3/C4: polling and cancel --------------------------------------------

/** One evaluated ply. Shared by C3's poll and C5's frozen report. */
export interface MoveEvaluation {
  ply: number
  move_number: number
  side: Side
  san: string
  best_san: string
  pv_san: string[]
  eval_before_cp: number
  eval_after_cp: number
  eval_before_win_pct: number
  eval_after_win_pct: number
  mate_before: number | null
  mate_after: number | null
  played_rank: number | null
  drop_win_pct: number
  classification: Classification
  tags: string[]
  suppressed: boolean
  suppress_reason: string
}

export type JobMove = MoveEvaluation
export type ReportMove = MoveEvaluation

export interface JobStatus {
  job_id: string
  status: JobStatusValue
  game_key: string
  user_color: Side
  progress_ply: number
  moves: JobMove[]
  error: string | null
}

export interface CancelResponse {
  job_id: string
  status: JobStatusValue
  cancelled: boolean
}

// --- C5: the frozen report ------------------------------------------------

export interface ReportEngine {
  name: string
  depth: number
  multipv: number
}

export interface ReportGame {
  white: string
  black: string
  result: string
  date: string
  event: string
  eco: string
  link: string
  user_color: Side
}

export interface ReportSummary {
  user_color: Side
  moves_analysed: number
  accuracy: number | null
  blunders: number
  mistakes: number
  inaccuracies: number
  ok: number
}

export interface GameReport {
  schema_version: 1
  engine: ReportEngine
  game: ReportGame
  moves: ReportMove[]
  summary: ReportSummary
}

// --- Phase 3 A1: positions + phase ---------------------------------------

export interface ReviewPosition {
  ply: number
  fen_before: string
  fen_after: string
  /** Describes fen_before: the position the mover faced. */
  phase: Phase
}

export interface GamePositions {
  game_key: string
  user_color: Side
  starting_fen: string
  positions: ReviewPosition[]
}

// --- Phase 3 D1: try-again grading ---------------------------------------

export interface TryRequest {
  user_color: Side
  ply: number
  move_san: string
}

export interface TryResponse {
  /** Always true on a 200; an illegal candidate is a 422. */
  legal: true
  accepted: boolean
  classification: Classification
  drop_win_pct: number
  eval_after_cp: number
  best_san: string
  pv_san: string[]
  threshold_win_pct: number
  reason: string
}

// --- /health (outside /api) ----------------------------------------------

export interface HealthResponse {
  status: 'ok' | 'degraded' | 'unavailable'
  database: 'ok' | 'out-of-date' | 'error'
  schema_version?: number
}
