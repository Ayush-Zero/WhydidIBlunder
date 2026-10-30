import { useId, useState, type FormEvent } from 'react'
import {
  DEFAULT_DISCOVERY_FILTERS,
  MAX_GAMES_MAX,
  MAX_GAMES_MIN,
  MONTHS_MAX,
  MONTHS_MIN,
  TIME_CLASSES,
  USERNAME_PATTERN,
  isTimeClass,
  type DiscoveryFilters,
  type DiscoverySearch,
} from '../lib/games'

interface SearchFormProps {
  initialUsername?: string
  initialFilters?: DiscoveryFilters
  showFilters?: boolean
  submitLabel?: string
  autoFocus?: boolean
  onSubmit: (search: DiscoverySearch) => void
}

export function SearchForm({
  initialUsername = '',
  initialFilters = DEFAULT_DISCOVERY_FILTERS,
  showFilters = false,
  submitLabel = 'Find games',
  autoFocus = false,
  onSubmit,
}: SearchFormProps) {
  const [error, setError] = useState<string | null>(null)
  const usernameId = useId()
  const monthsId = useId()
  const maxGamesId = useId()
  const timeClassId = useId()
  const errorId = useId()

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const username = String(data.get('username') ?? '').trim()

    if (!USERNAME_PATTERN.test(username)) {
      setError('Use 1-50 letters, numbers, underscores or hyphens.')
      return
    }

    let months = initialFilters.months
    let maxGames = initialFilters.max_games
    let timeClass = initialFilters.time_class
    let includeFromPosition = initialFilters.include_from_position

    if (showFilters) {
      months = Number(data.get('months'))
      if (
        !Number.isInteger(months) ||
        months < MONTHS_MIN ||
        months > MONTHS_MAX
      ) {
        setError(`Months must be a whole number from ${MONTHS_MIN} to ${MONTHS_MAX}.`)
        return
      }

      maxGames = Number(data.get('max_games'))
      if (
        !Number.isInteger(maxGames) ||
        maxGames < MAX_GAMES_MIN ||
        maxGames > MAX_GAMES_MAX
      ) {
        setError(
          `Max games must be a whole number from ${MAX_GAMES_MIN} to ${MAX_GAMES_MAX}.`,
        )
        return
      }

      const rawTimeClass = String(data.get('time_class') ?? '')
      if (rawTimeClass !== '' && !isTimeClass(rawTimeClass)) {
        setError('Choose a valid time class.')
        return
      }
      timeClass = isTimeClass(rawTimeClass) ? rawTimeClass : null
      includeFromPosition = data.get('include_from_position') === 'on'
    }

    setError(null)
    onSubmit({
      username,
      months,
      max_games: maxGames,
      time_class: timeClass,
      include_from_position: includeFromPosition,
    })
  }

  return (
    <form
      className={`search-form${showFilters ? ' search-form--filters' : ' search-form--hero'}`}
      role="search"
      noValidate
      onSubmit={handleSubmit}
    >
      <div className="search-field">
        <label htmlFor={usernameId}>Chess.com username</label>
        <div className="search-input-row">
          <input
            id={usernameId}
            name="username"
            type="text"
            defaultValue={initialUsername}
            placeholder="e.g. hikaru"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            maxLength={50}
            required
            autoFocus={autoFocus}
          />
          <button className="button" type="submit">
            {submitLabel}
          </button>
        </div>
      </div>

      {showFilters ? (
        <fieldset className="filter-grid">
          <legend>Filters</legend>

          <label className="filter-field" htmlFor={monthsId}>
            <span>Months</span>
            <input
              id={monthsId}
              name="months"
              type="number"
              min={MONTHS_MIN}
              max={MONTHS_MAX}
              step={1}
              defaultValue={initialFilters.months}
              inputMode="numeric"
            />
          </label>

          <label className="filter-field" htmlFor={maxGamesId}>
            <span>Max games</span>
            <input
              id={maxGamesId}
              name="max_games"
              type="number"
              min={MAX_GAMES_MIN}
              max={MAX_GAMES_MAX}
              step={1}
              defaultValue={initialFilters.max_games}
              inputMode="numeric"
            />
          </label>

          <label className="filter-field" htmlFor={timeClassId}>
            <span>Time class</span>
            <select
              id={timeClassId}
              name="time_class"
              defaultValue={initialFilters.time_class ?? ''}
            >
              <option value="">All time classes</option>
              {TIME_CLASSES.map((timeClass) => (
                <option key={timeClass} value={timeClass}>
                  {timeClass.charAt(0).toUpperCase() + timeClass.slice(1)}
                </option>
              ))}
            </select>
          </label>

          <label className="checkbox-field">
            <input
              name="include_from_position"
              type="checkbox"
              defaultChecked={initialFilters.include_from_position}
            />
            <span>
              Show custom-start games
              <span className="form-hint">
                They can be listed, but cannot be analysed because they have no
                ply 1.
              </span>
            </span>
          </label>
        </fieldset>
      ) : null}

      {error ? (
        <p id={errorId} className="form-error" role="alert">
          {error}
        </p>
      ) : null}
    </form>
  )
}
