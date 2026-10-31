import { formatEval, whiteBarPercent, type WhiteEval } from '../lib/review'

interface EvalBarProps {
  evaluation: WhiteEval
}

export function EvalBar({ evaluation }: EvalBarProps) {
  const label = formatEval(evaluation)
  const percent = whiteBarPercent(evaluation)

  return (
    <div className="eval">
      <div
        className="eval-bar"
        role="img"
        aria-label={
          evaluation.terminal
            ? `Evaluation: checkmate, ${
                evaluation.winPct >= 50 ? 'White' : 'Black'
              } wins`
            : `Evaluation: ${label} for White`
        }
      >
        <span
          className="eval-bar-fill"
          style={{ height: `${percent}%` }}
          aria-hidden="true"
        />
      </div>
      <p className="eval-caption">
        <span className="eval-value">{label}</span>
        <span className="eval-label">White POV</span>
      </p>
    </div>
  )
}
