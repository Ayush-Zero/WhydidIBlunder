# WhydidIBlunder web

The Phase 3 browser client. It is a thin client over the FastAPI service: it
does not download an engine and never talks to Chess.com directly.

## Scripts

```powershell
npm install
npm run dev        # Vite dev server on http://localhost:5173
npm test           # Vitest, offline
npm run test:e2e   # Playwright, offline
npm run lint       # oxlint
npm run build      # type-check + production bundle
npm run preview    # serve the built dist/ (proxy the API separately)
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

## Production deployment

`npm run build` writes a static `dist/`. Serve it from a reverse proxy (for
example Caddy) and proxy `/api/*` and `/health` to the FastAPI origin. The React
router uses history URLs, so unmatched application paths must fall back to
`index.html`; do not fall back API paths.

- **Same origin:** leave `VITE_API_BASE_URL=/api` at build time. No CORS origin
  is needed because the browser never makes a cross-origin request; set
  `CORS_ORIGINS=` explicitly to keep the allowlist empty.
- **Split origin:** rebuild with the API's absolute base URL (for example
  `https://api.example.com/api`) and list the frontend origin in
  `CORS_ORIGINS`. `allow_credentials` stays off; the API permits only `GET` and
  `POST`, and exposes the rate-limit headers browsers need.
- **Proxy controls:** terminate TLS at the proxy, set `TRUSTED_PROXY_IPS` to the
  proxy network, and apply a global request/body-size limit. The FastAPI
  per-IP rate limits are per process and do not replace a proxy limit.
- The build contains no engine binary and no provider key. `VITE_*` values are
  public by definition; never put a secret in `.env.local`.

See [`docs/ownership.md`](../docs/ownership.md) for the full ownership, CORS and
deployment requirements.

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

`npm run test:e2e` runs Playwright in Chromium against Vite with the same
recorded API fixtures routed in the browser. Install the browser once with
`npx playwright install chromium`. The suite covers the full
discovery → analysis → review → try-again journey, cancellation, and a durable
report reload without a backend or network.

The full-stack browser smoke is opt-in:

```powershell
$env:RUN_LIVE_TESTS='1'
npm run test:e2e -- e2e/live.spec.ts
Remove-Item Env:RUN_LIVE_TESTS
```

It starts the real API and a real worker with Stockfish through
`scripts/e2e_live_app.py`; Chess.com is still the recorded fixture. See
[`docs/testing.md`](../docs/testing.md) for the harness details.
