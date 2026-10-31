import { useState } from 'react'
import type { MoveEvaluation, ReportSummary } from '../api'
import {
  CLASSIFICATION_LABELS,
  stillAheadNote,
  tagLabel,
} from '../lib/review'

type FlagFilter = 'all' | 'blunder' | 'mistake' | 'inaccuracy'

interface MistakesPanelProps {
  /** The user's flagged moves, in report order. */
  moves: MoveEvaluation[]
  summary: ReportSummary
  selectedPly: number
  onSelect: (ply: number) => void
}

export function MistakesPanel({
  moves,
  summary,
  selectedPly,
  onSelect,
}: MistakesPanelProps) {
  const [filter, setFilter] = useState<FlagFilter>('all')
  const visible =
    filter === 'all'
      ? moves
      : moves.filter((move) => move.classification === filter)

  const filters: Array<{ value: FlagFilter; label: string; count: number }> = [
    {
      value: 'all',
      label: 'All',
      count: summary.blunders + summary.mistakes + summary.inaccuracies,
    },
    { value: 'blunder', label: 'Blunders', count: summary.blunders },
    { value: 'mistake', label: 'Mistakes', count: summary.mistakes },
    { value: 'inaccuracy', label: 'Inaccuracies', count: summary.inaccuracies },
  ]

  return (
    <section className="mistakes-panel" aria-labelledby="mistakes-title">
      <div className="mistakes-header">
        <h3 id="mistakes-title">Your mistakes</h3>
        <p>Click a flagged move to put it on the board.</p>
      </div>

      <div
        className="mistake-filters"
        role="group"
        aria-label="Filter flagged moves"
      >
        {filters.map((item) => (
          <button
            key={item.value}
            className="mistake-filter"
            type="button"
            aria-pressed={filter === item.value}
            onClick={() => setFilter(item.value)}
          >
            {item.label}
            <span className="mistake-filter-count">{item.count}</span>
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <p className="state-copy">{emptyCopy(filter)}</p>
      ) : (
        <ol className="mistake-list" aria-label="Flagged moves">
          {visible.map((move) => (
            <li key={move.ply}>
              <MistakeRow
                move={move}
                selected={move.ply === selectedPly}
                onSelect={onSelect}
              />
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

interface MistakeRowProps {
  move: MoveEvaluation
  selected: boolean
  onSelect: (ply: number) => void
}

function MistakeRow({ move, selected, onSelect }: MistakeRowProps) {
  const note = stillAheadNote(move)
  const sideMark = move.side === 'white' ? '.' : '…'

  return (
    <button
      type="button"
      className={`mistake-row${selected ? ' mistake-row--selected' : ''}`}
      aria-current={selected ? 'true' : undefined}
      onClick={() => onSelect(move.ply)}
    >
      <span className="mistake-move">
        <span className="mistake-number">
          {move.move_number}
          {sideMark}
        </span>
        <span className="mistake-san">{move.san}</span>
        <span className={`badge ${badgeTone(move.classification)}`.trim()}>
          {CLASSIFICATION_LABELS[move.classification]}
        </span>
      </span>
      <span className="mistake-drop">
        {move.drop_win_pct.toFixed(1)} win% drop
      </span>
      {move.tags.length > 0 ? (
        <span className="mistake-tags">
          {move.tags.map((tag) => (
            <span className="tag" key={tag}>
              {tagLabel(tag)}
            </span>
          ))}
        </span>
      ) : null}
      {note ? <span className="mistake-note">{note}</span> : null}
    </button>
  )
}

function badgeTone(classification: MoveEvaluation['classification']): string {
  if (classification === 'blunder') return 'badge--danger'
  if (classification === 'mistake') return 'badge--warning'
  return ''
}

function emptyCopy(filter: FlagFilter): string {
  switch (filter) {
    case 'blunder':
      return 'No blunders in this game.'
    case 'mistake':
      return 'No mistakes in this game.'
    case 'inaccuracy':
      return 'No inaccuracies in this game.'
    default:
      return 'No flagged moves in this game.'
  }
}
