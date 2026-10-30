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
Player search, the analysis lifecycle and the review board land in the next
milestones.

The client is in `src/api/`: `types.ts` mirrors the wire contract, `endpoints.ts`
builds the requests, `http.ts` owns fetch/timeout/error mapping, and `url.ts`
keeps opaque game keys out of raw paths. The browser still only talks to
`/api` and `/health`.
