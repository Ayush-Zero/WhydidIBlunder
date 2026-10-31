# WhydidIBlunder

Chess.com game analysis with a **server-side Stockfish** engine. Enter a
Chess.com username, pick a game, and get a move-by-move report that labels
inaccuracies, mistakes and blunders - with the engine's best line and the
reason each move was flagged.

The browser is intended to be a thin client. The analyser runs natively on the
server, not in WebAssembly, so the same code path powers the CLI and the API.

## Status

- **Phase 1 - analyzer core:** complete. The output schema is frozen and
  covered by offline tests plus a golden-game fixture.
- **Phase 2 - API + worker:** complete. The API lists games, queues and polls
  analyses, cancels them, serves stored reports, returns an explain stub, and
  rate-limits new analyses. Milestone D (end-to-end verification, cache
  correctness and the safety review) is signed off; see
  [`docs/safety-review.md`](docs/safety-review.md).
- **Phase 3 - frontend:** complete. The browser adds player search and game
  discovery, the poll/cancel analysis lifecycle, a durable report URL, a
  board-first review screen with a White-POV eval bar, mistakes and arrows/PV,
  and server-graded try-again. Playwright covers the mocked journey plus an
  opt-in full-stack smoke; see [`web/README.md`](web/README.md) and
  [`docs/testing.md`](docs/testing.md).

> **Phase 0 gate:** satisfied on 2026-10-30. The owner reduced the validation
> sample from about 20 games to 5 and agreed with the flags; the threshold
> values are unchanged. Phase 3 can start.

## What works today

- Chess.com game list, with bots and from-position games handled explicitly.
- SQLite-backed job queue with an atomic claim, stale-job reaper, hard timeout
  and cooperative cancellation.
- A separate worker process that persists every analysed ply, so polling shows
  real partial progress.
- A position cache keyed by normalized FEN, engine version, depth and MultiPV.
- FastAPI endpoints for listing, queueing, polling, cancelling, reports,
  try-again grading and the explain placeholder.
- A read-only position bridge that replays a stored report into per-ply FENs and
  phase labels for the review board.
- A routed frontend shell with a same-origin dev proxy, a typed API client,
  mapped transport errors, an error boundary and offline build/test/lint
  scripts.
- Player discovery: username search, time-class and archive filters, game ratings,
  results, bot/custom-start badges and explicit empty/error states.
- Analysis lifecycle from a discovered game: start or resume a server-side job,
  poll real partial progress, cancel once, ignore stale responses, and preserve
  the saved prefix on failure or cancellation. Reloading the job URL resumes it.
- A board-first review screen with grouped moves, a White-POV eval bar,
  accuracy/mistake summary, phase breakdown, played/best arrows and a
  click-through PV. Durable report links survive reloads.
- Server-graded try-again: the browser offers legal moves, but the API
  reconstructs the position, re-validates the SAN and runs Stockfish for the
  candidate's after-position.
- Per-IP rate limiting on `POST /api/analyze` and `POST .../try`; try-again also
  has a one-slot non-blocking engine guard and a hard timeout.

## Quick start (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env
# Edit .env and set STOCKFISH_PATH to your Stockfish binary.

# Terminal 1 - the worker owns the engine and initialises the schema.
.\.venv\Scripts\python.exe -m server.worker_main

# Terminal 2 - the API.
.\.venv\Scripts\python.exe -m server.main
```

Then open <http://127.0.0.1:8000/api/docs>.

The analysis worker remains a separate process: a full game is long, CPU-bound and
would monopolise request handling. Try-again is a single short search that runs in
the API process under a one-slot guard and a hard timeout, so it cannot turn the
API into a second unbounded worker. Scaling full analyses still means starting
more workers; the atomic claim makes that safe.

## Frontend (Phase 3)

The React client lives in [`web/`](web/). Its dev server proxies `/api` and
`/health` to the API, so the browser stays same-origin during local work.

```powershell
cd web
Copy-Item .env.example .env.local   # optional; the defaults match the API above
npm install
npm run dev                         # http://localhost:5173
```

Run the API (and, for analysis, the worker) in the other terminals. The frontend
scripts are:

```powershell
npm test           # Vitest, offline
npm run test:e2e   # Playwright, offline
npm run lint       # oxlint
npm run build      # type-check + production bundle
```

For a split deployment, set `VITE_API_BASE_URL` to the API's absolute base URL.
The default `/api` assumes the built `dist/` is served from the API origin. See
[`web/README.md`](web/README.md) for the details.

## Tests

The default suite is offline - no network and no engine:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Live engine tests are opt-in:

```powershell
$env:RUN_LIVE_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -m live
Remove-Item Env:RUN_LIVE_TESTS
```

See [`docs/testing.md`](docs/testing.md) for what each layer protects.

Frontend tests are offline too (Vitest, React Testing Library and MSW):

```powershell
cd web
npm test
npm run test:e2e
npm run lint
npm run build
```

The default Playwright suite routes the recorded API fixtures in the browser, so
it needs no API, worker or Stockfish. Install Chromium once with
`npx playwright install chromium`. The full-stack browser smoke is opt-in:

```powershell
$env:RUN_LIVE_TESTS = "1"
cd web
npm run test:e2e -- e2e/live.spec.ts
Remove-Item Env:RUN_LIVE_TESTS
```

See [`docs/testing.md`](docs/testing.md) for both browser layers and the live
harness.

## Documentation

- [`web/README.md`](web/README.md) - frontend scripts, dev proxy and API boundary.
- [`docs/api.md`](docs/api.md) - endpoint contracts, defaults and error mapping.
- [`docs/ownership.md`](docs/ownership.md) - **security, ownership and abuse
  model** (C8, updated for Phase 3 D5).
- [`docs/safety-review.md`](docs/safety-review.md) - **D5 safety review**: no
  shell, bound SQL, constructed engine input, the try-again boundary and the
  edge-case policies.
- [`docs/storage.md`](docs/storage.md) - schema and cache-key decisions.
- [`docs/queue.md`](docs/queue.md) - atomic claim and cancellation.
- [`docs/worker.md`](docs/worker.md) - worker loop and persistence ordering.
- [`docs/cache.md`](docs/cache.md) - the position cache key.
- [`docs/analyzer-schema.md`](docs/analyzer-schema.md) - every report field,
  its units and point of view.

## Security and ownership

v1 has **no accounts and no authorization layer**. This is a deliberate
portfolio-scope decision, not an accident:

- A `job_id` is a random UUID4 and acts as a bearer capability. **Anyone who
  has it can poll or cancel that job.**
- A `game_key` and `user_color` identify a public Chess.com game and side.
  **Anyone who knows them can fetch the stored report and positions, and ask the
  server to grade a legal try-again candidate.** Neither is a secret.
- IDs never contain secrets, emails, API keys or signed payloads.
- Expensive analysis and try-again are limited per IP, and `X-Forwarded-For` is
  trusted only from a configured proxy. Try-again also has one engine slot and a
  hard timeout.

Read [`docs/ownership.md`](docs/ownership.md) for the full threat model, the
rate-limit limitations, deployment requirements, and the conditions under
which authentication must be added.

## License

AGPL-3.0. See [`LICENSE`](LICENSE).
