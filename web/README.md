# WhydidIBlunder web

The Phase 3 browser client. It is a thin client over the FastAPI service: it
does not download an engine and never talks to Chess.com directly.

## Scripts

```powershell
npm install
npm run dev        # Vite dev server on http://localhost:5173
npm test           # Vitest, offline
npm run lint       # oxlint
npm run build      # type-check + production bundle
npm run preview    # serve the production build
```

## API boundary

`VITE_API_BASE_URL` defaults to `/api`. In development, Vite proxies `/api` and
`/health` to the FastAPI process at `http://127.0.0.1:8000`, so the browser is
same-origin and local work does not depend on a permissive CORS list.

Copy `.env.example` to `.env.local` only if the API runs elsewhere:

```powershell
Copy-Item .env.example .env.local
```

For a split production deployment, build with an absolute
`VITE_API_BASE_URL`; for the normal deployment, serve the built `dist/` from the
same origin as the API and leave the default in place.

## Current state

Milestone A3 adds the typed transport client for C1-C5, positions and try; the
fixture-tested abort/timeout handling and mapped API errors; and the shell's
server-status indicator, error boundary and shared loading/empty/error states.
Milestone B1 adds username search at `/player/:username`, time-class/archive
filters, game ratings and results, bot/custom-start badges, and the player
discovery states. Milestone B2 adds the analysis lifecycle: Analyze starts or
resumes a C2 job, `/player/:username/analysis/:jobId` polls C3 serially, cancel
uses C4 once, and the page handles done, failed, cancelled, stale polls,
unmounts and recoverable errors while preserving the saved prefix. Reloading a
job URL resumes polling, and Back to games keeps the discovery filters.

Milestone B3 adds the durable report URL
`/player/:username/report?game_key=…&user_color=…`. Opaque keys stay in the
query string, a direct visit reloads the same report, and a stored report
returned with `job_id: null` navigates there instead of ending in a notice.
That navigation pushes a history entry, so browser back/forward moves between
discovery and the report. Incomplete links and expired reports show a recovery
path.

The review screen is board-first: a selected-ply board, a grouped move list,
White-POV evaluations, `Moves` / `Mistakes` tabs, arrows and best-line
click-through. Flagged user moves can open Try again, which asks the server to
grade one legal candidate move; the browser never runs an engine or stores a
classification.

The client is in `src/api/`: `types.ts` mirrors the wire contract, `endpoints.ts`
builds the requests, `http.ts` owns fetch/timeout/error mapping, and `url.ts`
keeps opaque game keys out of raw paths. The browser still only talks to
`/api` and `/health`.

## Tests

`npm test` runs the offline Vitest suite. Discovery tests use MSW request
handlers instead of live services. The analysis lifecycle tests pair MSW with
fake timers, and `src/hooks/useAnalysisJob.test.tsx` uses fake timers and a
mocked client to pin the polling scheduler. No test requires the API, the
worker, Stockfish or Chess.com.
