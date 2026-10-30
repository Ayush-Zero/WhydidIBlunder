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
  [`docs/phase2_breakdown.md`](docs/phase2_breakdown.md) and
  [`docs/safety-review.md`](docs/safety-review.md).
- **Phase 3 - frontend:** not started.

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

## Documentation

- [`docs/phase1_breakdown.md`](docs/phase1_breakdown.md) - analyzer work
  breakdown and frozen contract.
- [`docs/phase2_breakdown.md`](docs/phase2_breakdown.md) - API + worker plan
  and current status.
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
