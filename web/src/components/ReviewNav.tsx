import type { MoveEvaluation } from '../api'

interface ReviewNavProps {
  moves: MoveEvaluation[]
  selectedPly: number
  onSelect: (ply: number) => void
}

/** Step through plies without relying on the board or the move list. */
export function ReviewNav({ moves, selectedPly, onSelect }: ReviewNavProps) {
  const index = moves.findIndex((move) => move.ply === selectedPly)
  const previous = index > 0 ? moves[index - 1] : undefined
  const next =
    index >= 0 && index < moves.length - 1 ? moves[index + 1] : undefined

  return (
    <div className="review-nav" role="group" aria-label="Move navigation">
      <button
        className="button button--secondary button--small"
        type="button"
        disabled={!previous}
        onClick={() => {
          if (previous) onSelect(previous.ply)
        }}
      >
        Previous move
      </button>
      <button
        className="button button--secondary button--small"
        type="button"
        disabled={!next}
        onClick={() => {
          if (next) onSelect(next.ply)
        }}
      >
        Next move
      </button>
    </div>
  )
}
