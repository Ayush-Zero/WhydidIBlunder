import { useRef, type KeyboardEvent } from 'react'
import type { MoveEvaluation } from '../api'
import { formatEval, groupMoves, whitePovEval } from '../lib/review'

interface MoveListProps {
  moves: MoveEvaluation[]
  selectedPly: number
  onSelect: (ply: number) => void
}

export function MoveList({ moves, selectedPly, onSelect }: MoveListProps) {
  const listRef = useRef<HTMLOListElement>(null)
  const rows = groupMoves(moves)
  const plies = moves.map((move) => move.ply)

  function select(nextIndex: number) {
    const ply = plies[nextIndex]
    if (ply === undefined) return
    onSelect(ply)
    listRef.current
      ?.querySelector<HTMLButtonElement>(`button[data-ply="${ply}"]`)
      ?.focus()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLOListElement>) {
    const index = plies.indexOf(selectedPly)

    switch (event.key) {
      case 'ArrowLeft':
      case 'ArrowUp':
        event.preventDefault()
        select(Math.max(0, index - 1))
        break
      case 'ArrowRight':
      case 'ArrowDown':
        event.preventDefault()
        select(Math.min(plies.length - 1, index + 1))
        break
      case 'Home':
        event.preventDefault()
        select(0)
        break
      case 'End':
        event.preventDefault()
        select(plies.length - 1)
        break
    }
  }

  return (
    <ol
      ref={listRef}
      className="move-list"
      aria-label="Moves"
      onKeyDown={handleKeyDown}
    >
      {rows.map((row) => (
        <li className="move-row" key={row.moveNumber}>
          <span className="move-number" aria-hidden="true">
            {row.moveNumber}.
          </span>
          <MoveButton
            move={row.white}
            selectedPly={selectedPly}
            onSelect={onSelect}
          />
          <MoveButton
            move={row.black}
            selectedPly={selectedPly}
            onSelect={onSelect}
          />
        </li>
      ))}
    </ol>
  )
}

interface MoveButtonProps {
  move: MoveEvaluation | null
  selectedPly: number
  onSelect: (ply: number) => void
}

function MoveButton({ move, selectedPly, onSelect }: MoveButtonProps) {
  if (!move) return <span className="move-empty" aria-hidden="true" />

  const selected = move.ply === selectedPly
  const after = whitePovEval(move, 'after')
  const side = move.side === 'white' ? 'White' : 'Black'

  return (
    <button
      type="button"
      className={`move-button${selected ? ' move-button--selected' : ''}`}
      data-ply={move.ply}
      tabIndex={selected ? 0 : -1}
      aria-current={selected ? 'true' : undefined}
      aria-label={`${side} move ${move.move_number}, ${move.san}, evaluation ${formatEval(after)}`}
      onClick={() => onSelect(move.ply)}
    >
      <span className="move-san">{move.san}</span>
      <span className="move-eval">{formatEval(after)}</span>
    </button>
  )
}
