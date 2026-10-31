# Testing

## Offline by default

`pytest` **never** launches Stockfish. The whole suite is pure logic plus frozen
fixtures (plus one killed-helper-process reaper test), so it runs anywhere in
well under a minute:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Anything that needs a real engine is opt-in — see *The opt-in live test* below.

## The opt-in live test

`tests/test_live_engine.py` is marked `@pytest.mark.live` and launches a real
Stockfish binary. It is **deselected by default** (`addopts = -m "not live"` in
`pytest.ini`), and `tests/conftest.py` additionally skips it unless
`RUN_LIVE_TESTS=1` is set — so a plain `pytest` run never touches the engine.

It checks **plumbing only**: the engine starts and returns a score, MultiPV
returns the requested number of lines, and `analyze_game` produces a
schema-valid, JSON-serialisable report. It never asserts *which* moves are
flagged.

`tests/test_e2e_service.py` is also `live` and is the Phase 2 **D1**
done-criterion: it drives `POST /api/analyze` over recorded Chess.com fixtures,
runs the real worker (and therefore real Stockfish) to drain the job, polls
`GET /api/jobs/{id}`, then fetches the stored report from
`GET /api/games/{game_key}/report`. It asserts the full contract — the report
validates against the frozen schema, is served verbatim, and a second request
reuses it without requeueing — but again not which moves are flagged.

`tests/test_e2e_cancel.py` is `live` too and is **D2**: while a real worker is
mid-analysis in another thread, the API cancels the job. It asserts the job
settles `cancelled` (not `done`), the persisted prefix survives and is what C3
returns, no report is written, and the Stockfish process has exited (checked by
pid). It is run repeatedly in review because it is the one test that depends on
timing.

`tests/test_e2e_reaper.py` is **D3** and is **not** live: it kills a helper
*process* mid-analysis, then lets the next worker's scheduled reaper requeue and
complete the job. The helper uses a slow fake engine, so no Stockfish and no
network are involved; a real engine crash cannot be cleaned up from inside the
killed process (that is D2's job). `JOB_TIMEOUT_SECONDS=0` stands in for the
production 180-second timeout.

```powershell
$env:RUN_LIVE_TESTS='1'
.\.venv\Scripts\python.exe -m pytest -m live
Remove-Item Env:RUN_LIVE_TESTS   # back to offline
```

Requires Stockfish at `STOCKFISH_PATH` (`C:\Tools\stockfish\stockfish.exe`).

## Frontend

The React client's default suite is offline and does not need the API or an
engine:

```powershell
cd web
npm test
npm run lint
npm run build
```

A2 covers the routed shell, the API URL boundary and the build. A3 adds the
typed transport client, fixture-parsing and error-mapping tests, the shell error
boundary, and shared loading/empty/error states. The component and mocked-API
suites grow with the screens in Phase 3.

### Browser E2E

The Playwright suite drives the real browser bundle against the recorded API
fixtures. It is offline and engine-free by default:

```powershell
cd web
npm install
npx playwright install chromium   # once per machine
npm run test:e2e
```

`web/playwright.config.ts` starts Vite on port 4173. The test routes `/api` and
`/health` through `page.route`, so no API process, worker, Stockfish or Chess.com
request is involved. `journey.spec.ts` covers search → game list → analyze →
progress → report → mistake → arrows/PV → try-again, cancellation with the saved
prefix, and a durable report reload. Progress is held until the test releases it;
the suite waits on UI state rather than fixed sleeps. Traces and screenshots for
failures land in `web/test-results/`.

### The opt-in full-stack browser smoke

`live.spec.ts` is skipped unless `RUN_LIVE_TESTS=1`. It uses the real FastAPI app,
a separate real worker process and Stockfish; only Chess.com is served from the
recorded fixtures, so it remains deterministic and network-free:

```powershell
$env:RUN_LIVE_TESTS='1'
cd web
npm run test:e2e -- e2e/live.spec.ts
Remove-Item Env:RUN_LIVE_TESTS   # back to the offline suite
```

`scripts/e2e_live_app.py` boots the API on port 8001 with the fixture transport,
starts the worker when the browser queues the four-ply game, and lets Vite proxy
to it. It needs the repository `.venv` and `STOCKFISH_PATH` just like the Python
live tests. `E2E_PYTHON` can override the interpreter path.

## What the suite protects

| Layer | File | Guards against |
|---|---|---|
| Pure-logic units | `tests/test_pov.py`, `test_terminal.py`, `test_classification.py`, `test_accuracy.py`, `test_phase.py`, `test_tags.py` | the maths drifting (win%, thresholds, accuracy, POV, mate encoding) |
| Engine wiring | `tests/test_engine_reuse.py` | a re-search creeping back in (2n engine calls instead of n+1) and settings going hardcoded |
| Cache key logic | `tests/test_cache.py` | the cache poisoning itself: a key that ignores the halfmove clock or the engine build, or a shallow row answering a deep request |
| Cache in front of the engine | `tests/test_cache_wiring.py` | the cache silently not being consulted, or surviving an engine bump |
| Durable cache key | `tests/test_position_cache.py` | the SQLite backend inventing its own normalisation, serving across an engine/key-version bump, letting a shallow row overwrite a deep one, or duplicating a row on re-store |
| Safety boundary | `tests/test_safety.py` | a shell (`shell=True`, `os.system`/`os.popen`) creeping into production code; the scanner itself going blind |
| Storage | `tests/test_db.py` | WAL not actually set, migrations not idempotent, keys drifting from the plan, a migration that only runs on a *fresh* database |
| Job queue | `tests/test_queue.py` | two workers claiming one job, contention surfacing as `database is locked`, a retried job wearing its last error |
| Worker loop | `tests/test_worker.py` | a job stuck `running` after a failure or a cancel, progress not advancing, plies lost on cancel, the durable cache not actually saving searches |
| Chess.com client | `tests/test_chesscom.py` | a 404-for-empty-month crashing, bots/from-position games slipping through, an uncached or non-atomic response cache |
| API skeleton | `tests/test_app.py` | `/health` reporting healthy while the database is unusable; CORS opening up |
| Report positions | `tests/test_positions.py`, `tests/test_api_positions.py` | SAN replay drifting from the stored report, FENs/phases being wrong, a corrupt report becoming a partial board, or positions leaking between colours |
| Frozen fixture, self-consistency | `tests/test_report.py` | a report that no longer recomputes from its own evals |
| Frozen fixture, exact flags | `tests/test_golden_game.py` | a *silently* different verdict on the sample game |
| Live smoke (opt-in) | `tests/test_live_engine.py` | the engine plumbing breaking (start, score, valid report) — never the classification |
| Service E2E (opt-in) | `tests/test_e2e_service.py` | the C2 → worker → C3 → C5 spine breaking: a job that never runs, a report that does not validate or is reshaped by the API, or a finished report being requeued |
| Cancel E2E (opt-in) | `tests/test_e2e_cancel.py` | a cancel being ignored, a stopped job writing a report, partial plies lost, or a Stockfish process orphaned after the worker exits |
| Reaper E2E | `tests/test_e2e_reaper.py` | a killed worker leaving a job `running` forever, a reaped job losing its partial plies, or a retry reusing the dead worker's attempt |
| Browser journey | `web/e2e/journey.spec.ts` | routing, review and cancel breaking in a real browser against the mocked contract; a reload losing the durable report link |
| Browser live smoke (opt-in) | `web/e2e/live.spec.ts` | the browser, API, worker and Stockfish failing to agree on one real analysis |

## The golden fixture

`tests/fixtures/immortal_game.report.json` is a frozen snapshot of

```text
analyze_game(samples/immortal_game.pgn, "white", depth=12, multipv=4)
```

taken with the configured Stockfish. Its `engine` header records the engine
name, depth and multipv, and `tests/test_golden_game.py` **pins** that header
plus the game's exact flagged plies. That is what makes an engine bump fail
loudly and instructively instead of quietly changing the numbers.

### Regenerating

Deliberate only — an engine bump, a depth change, or a change to what we emit
(for example the C2 tag wiring). It overwrites the fixture, so expect a large
but *reviewable* diff:

```powershell
.\.venv\Scripts\python.exe scripts\gen_fixture.py `
    --pgn-file=samples\immortal_game.pgn --color=white --depth=12 `
    --out=tests/fixtures/immortal_game.report.json
```

Then:

1. Read the diff move by move (`git diff tests/fixtures/immortal_game.report.json`).
2. Update `EXPECTED_ENGINE` / `EXPECTED_DEPTH` / `EXPECTED_MULTIPV` and the
   `EXPECTED_FLAGS` / `EXPECTED_TAGS` / `EXPECTED_SUMMARY` tables in
   `tests/test_golden_game.py`.
3. Update the constants **only once you agree with the new calls** — those
   tables record the agreed reading of the sample game, not merely whatever the
   engine emitted.

### Determinism requirement

Reproducible only if the engine settings match the ones it was generated with,
so the generator must run with the configured configuration:

* `STOCKFISH_THREADS=1` — a multi-threaded search is **not** deterministic, and
  a fixture generated that way can never be regenerated (it has no unique value).
* The same Stockfish binary. The engine *name* is pinned in the test, but the
  name does not change between builds that search differently.
* The same `depth` and `multipv`, both pinned in the test.

Two back-to-back fresh runs are byte-identical under these settings; that is the
property the fixture relies on.

> **A note on the fixture history:** the fixture originally committed in
> Milestone A did *not* reproduce under this configuration — its evals and
> engine lines came from a different engine setup and it disagreed with the live
> output. It was regenerated during Milestone C, where the regenerated baseline
> (1 blunder / 3 mistakes / 3 inaccuracies across both sides) finally matches
> what every live check of the sample has reported.

## It will not match Chess.com

Accuracy and the labels are our own model of a move's quality — a Lichess-style
win-probability curve plus win%-point thresholds. They are close in spirit to
Chess.com's numbers but are **not** guaranteed to equal them, and a shallower
depth (`ANALYSIS_DEPTH`) will disagree with a deeper one on sharp positions.
