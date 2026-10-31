import type { PvLine } from '../lib/review'

interface BestLineProps {
  line: PvLine | null
  error: string | null
  activeIndex: number
  onSelect: (index: number) => void
  onReset: () => void
}

/**
 * The engine's best line, with one click per PV position.
 *
 * `activeIndex` is the number of PV moves applied to `fen_before`; zero shows
 * the selected report ply. The parent resets it when the selection changes.
 */
export function BestLine({
  line,
  error,
  activeIndex,
  onSelect,
  onReset,
}: BestLineProps) {
  return (
    <section className="best-line" aria-labelledby="best-line-title">
      <div className="best-line-header">
        <h3 id="best-line-title">Best line</h3>
        {line ? (
          <button
            className="button button--secondary button--small"
            type="button"
            disabled={activeIndex === 0}
            onClick={onReset}
          >
            Reset
          </button>
        ) : null}
      </div>

      {error ? <p className="best-line-error">{error}</p> : null}

      {line ? (
        <>
          <ol className="best-line-moves" aria-label="Best line moves">
            {line.moves.map((san, index) => {
              const moveIndex = index + 1
              const active = moveIndex === activeIndex

              return (
                <li key={`${moveIndex}-${san}`}>
                  <button
                    className={`best-line-move${
                      active ? ' best-line-move--active' : ''
                    }`}
                    type="button"
                    aria-pressed={active}
                    onClick={() => onSelect(moveIndex)}
                  >
                    {san}
                  </button>
                </li>
              )
            })}
          </ol>
          <p className="best-line-status" role="status">
            {activeIndex === 0
              ? 'Showing the selected position.'
              : `Showing best-line move ${activeIndex} of ${line.moves.length}.`}
          </p>
        </>
      ) : null}
    </section>
  )
}
