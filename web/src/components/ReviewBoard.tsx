import { Chessboard, type Arrow } from 'react-chessboard'
import type { Side } from '../api'

interface ReviewBoardProps {
  fen: string
  orientation: Side
  arrows?: Arrow[]
}

export function ReviewBoard({ fen, orientation, arrows }: ReviewBoardProps) {
  return (
    <div className="review-board" data-testid="review-board" data-fen={fen}>
      <Chessboard
        options={{
          position: fen,
          boardOrientation: orientation,
          allowDragging: false,
          showAnimations: false,
          arrows,
        }}
      />
    </div>
  )
}
