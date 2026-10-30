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
  [`opencode/phase2_breakdown.md`](opencode/phase2_breakdown.md) and
  [`docs/safety-review.md`](docs/safety-review.md).
- **Phase 3 - frontend:** in progress. A1 (the positions + phase bridge), A2
  (the routed Vite shell) and A3 (the typed API client + app shell) are complete;
  the work breakdown is
  [`opencode/phase3_breakdown.md`](opencode/phase3_breakdown.md).

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
- FastAPI endpoints for listing, queueing, polling, cancelling, reports and the
  explain placeholder.
- A read-only position bridge that replays a stored report into per-ply FENs and
  phase labels for the review board.
- A routed frontend shell with a same-origin dev proxy, a typed API client,
  mapped transport errors, an error boundary and offline build/test/lint
  scripts.
- Per-IP rate limiting on `POST /api/analyze`.

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

The API and worker are separate processes on purpose: Stockfish is CPU-bound
and would block request handling if it ran in the API process. Scaling means
starting more workers; the atomic claim makes that safe.

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
npm test        # Vitest, offline
npm run lint    # oxlint
npm run build   # type-check + production bundle
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

Frontend tests are offline too:

```powershell
cd web
npm test
npm run lint
npm run build
```

## Documentation

- [`opencode/phase1_breakdown.md`](opencode/phase1_breakdown.md) - analyzer work
  breakdown and frozen contract.
- [`opencode/phase2_breakdown.md`](opencode/phase2_breakdown.md) - API + worker
  plan and current status.
- [`opencode/phase3_breakdown.md`](opencode/phase3_breakdown.md) - frontend and
  interactive-review plan.
- [`web/README.md`](web/README.md) - frontend scripts, dev proxy and API boundary.
- [`docs/api.md`](docs/api.md) - endpoint contracts, defaults and error mapping.
- [`docs/ownership.md`](docs/ownership.md) - **security, ownership and abuse
  model** (C8).
- [`docs/safety-review.md`](docs/safety-review.md) - **D5 safety review**: no
  shell, bound SQL, constructed engine input, and the edge-case policies.
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
  **Anyone who knows them can fetch the stored report.** Neither is a secret.
- IDs never contain secrets, emails, API keys or signed payloads.
- Expensive analysis is limited per IP, and `X-Forwarded-For` is trusted only
  from a configured proxy.

Read [`docs/ownership.md`](docs/ownership.md) for the full threat model, the
rate-limit limitations, deployment requirements, and the conditions under
which authentication must be added.

## License

AGPL-3.0. See [`LICENSE`](LICENSE).
