import { Chessboard } from 'react-chessboard'
import type { Side } from '../api'

interface ReviewBoardProps {
  fen: string
  orientation: Side
}

export function ReviewBoard({ fen, orientation }: ReviewBoardProps) {
  return (
    <div className="review-board" data-testid="review-board" data-fen={fen}>
      <Chessboard
        options={{
          position: fen,
          boardOrientation: orientation,
          allowDragging: false,
          showAnimations: false,
        }}
      />
    </div>
  )
}
