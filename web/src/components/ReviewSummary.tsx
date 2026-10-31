import type { ReportSummary } from '../api'
import type { PhaseCounts } from '../lib/review'

interface ReviewSummaryProps {
  summary: ReportSummary
  phaseCounts: PhaseCounts[]
}

const PHASE_LABELS: Record<PhaseCounts['phase'], string> = {
  opening: 'Opening',
  middlegame: 'Middlegame',
  endgame: 'Endgame',
}

export function ReviewSummary({ summary, phaseCounts }: ReviewSummaryProps) {
  const accuracy =
    summary.accuracy === null ? '—' : `${summary.accuracy.toFixed(1)}%`

  return (
    <section className="review-summary" aria-labelledby="review-summary-title">
      <div className="review-summary-header">
        <h3 id="review-summary-title">Your summary</h3>
        <p className="summary-note">
          Accuracy uses the Lichess formula and may differ from Chess.com.
        </p>
      </div>

      <dl className="summary-stats">
        <div className="summary-stat">
          <dt>Accuracy</dt>
          <dd>{accuracy}</dd>
        </div>
        <div className="summary-stat summary-stat--blunder">
          <dt>Blunders</dt>
          <dd>{summary.blunders}</dd>
        </div>
        <div className="summary-stat summary-stat--mistake">
          <dt>Mistakes</dt>
          <dd>{summary.mistakes}</dd>
        </div>
        <div className="summary-stat summary-stat--inaccuracy">
          <dt>Inaccuracies</dt>
          <dd>{summary.inaccuracies}</dd>
        </div>
      </dl>

      <div className="phase-breakdown">
        <h4>Flagged moves by phase</h4>
        <ul className="phase-list">
          {phaseCounts.map((row) => (
            <li className="phase-row" key={row.phase}>
              <span className="phase-name">{PHASE_LABELS[row.phase]}</span>
              <span className="phase-flags">{phaseCopy(row)}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

function phaseCopy(row: PhaseCounts): string {
  const parts: string[] = []

  if (row.blunders > 0) {
    parts.push(countLabel(row.blunders, 'blunder', 'blunders'))
  }
  if (row.mistakes > 0) {
    parts.push(countLabel(row.mistakes, 'mistake', 'mistakes'))
  }
  if (row.inaccuracies > 0) {
    parts.push(countLabel(row.inaccuracies, 'inaccuracy', 'inaccuracies'))
  }

  return parts.length > 0 ? parts.join(', ') : 'No flagged moves'
}

function countLabel(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`
}
