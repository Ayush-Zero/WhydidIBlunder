import { useRef, useState, type KeyboardEvent } from 'react'
import type { MoveEvaluation, ReportSummary } from '../api'
import { MistakesPanel } from './MistakesPanel'
import { MoveList } from './MoveList'

const TABS = ['moves', 'mistakes'] as const
type PanelTab = (typeof TABS)[number]

interface ReviewPanelsProps {
  moves: MoveEvaluation[]
  flaggedMoves: MoveEvaluation[]
  summary: ReportSummary
  selectedPly: number
  onSelect: (ply: number) => void
}

/**
 * The move list and mistakes share one tabbed panel.
 *
 * Standard tabs: arrow keys move between tabs, Home/End jump to the ends, and
 * only the active panel is exposed to assistive technology.
 */
export function ReviewPanels({
  moves,
  flaggedMoves,
  summary,
  selectedPly,
  onSelect,
}: ReviewPanelsProps) {
  const [tab, setTab] = useState<PanelTab>('moves')
  const tabsRef = useRef<HTMLDivElement>(null)
  const flagged =
    summary.blunders + summary.mistakes + summary.inaccuracies

  function select(nextTab: PanelTab) {
    setTab(nextTab)
    tabsRef.current
      ?.querySelector<HTMLButtonElement>(`button[data-tab="${nextTab}"]`)
      ?.focus()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const index = TABS.indexOf(tab)
    const last = TABS.length - 1

    switch (event.key) {
      case 'ArrowLeft':
        event.preventDefault()
        select(TABS[index === 0 ? last : index - 1])
        break
      case 'ArrowRight':
        event.preventDefault()
        select(TABS[index === last ? 0 : index + 1])
        break
      case 'Home':
        event.preventDefault()
        select(TABS[0])
        break
      case 'End':
        event.preventDefault()
        select(TABS[last])
        break
    }
  }

  return (
    <div className="review-panels">
      <div
        ref={tabsRef}
        className="review-tabs"
        role="tablist"
        aria-label="Review panels"
        onKeyDown={handleKeyDown}
      >
        <button
          id="review-moves-tab"
          className="review-tab"
          type="button"
          role="tab"
          data-tab="moves"
          aria-selected={tab === 'moves'}
          aria-controls="review-moves-panel"
          tabIndex={tab === 'moves' ? 0 : -1}
          onClick={() => setTab('moves')}
        >
          Moves
        </button>
        <button
          id="review-mistakes-tab"
          className="review-tab"
          type="button"
          role="tab"
          data-tab="mistakes"
          aria-label={`Mistakes, ${flagged} flagged`}
          aria-selected={tab === 'mistakes'}
          aria-controls="review-mistakes-panel"
          tabIndex={tab === 'mistakes' ? 0 : -1}
          onClick={() => setTab('mistakes')}
        >
          Mistakes <span aria-hidden="true">{flagged}</span>
        </button>
      </div>

      <div
        id="review-moves-panel"
        className="review-tabpanel"
        role="tabpanel"
        aria-labelledby="review-moves-tab"
        hidden={tab !== 'moves'}
      >
        <MoveList
          moves={moves}
          selectedPly={selectedPly}
          onSelect={onSelect}
        />
      </div>

      <div
        id="review-mistakes-panel"
        className="review-tabpanel"
        role="tabpanel"
        aria-labelledby="review-mistakes-tab"
        hidden={tab !== 'mistakes'}
      >
        <MistakesPanel
          moves={flaggedMoves}
          summary={summary}
          selectedPly={selectedPly}
          onSelect={onSelect}
        />
      </div>
    </div>
  )
}
