# The worker (Phase 2 B3)

`server/worker.py` claims a job (B2), analyses it ply by ply (Phase 1), persists
the results, and finalises the job. `server/position_cache.py` is the durable
half of B1's cache, which the worker drives.

- **Implementation:** `server/worker.py`, `server/position_cache.py`,
  `server/worker_main.py`
- **Tests:** `tests/test_worker.py`
- **Run it:** `python -m server.worker_main` (add `--stop-after=N` to drain a
  queue and exit)

The worker is a **separate process** from the API on purpose: Stockfish is
CPU-bound and would otherwise block the request-serving event loop. Scaling is
"start more of these" — B2's claim makes concurrent workers safe, with no code
change.

## The three decisions that shape it

### 1. Plies are written as they happen, progress with them

`analyzer.pipeline.iter_game_moves` yields **one ply at a time**, and the worker
persists a `moves` row and calls `record_progress` for each. Two things fall out
of that for free:

- the poll endpoint (C3) can show real partial results — they are already rows;
- the heartbeat is meaningful, because progress and liveness are recorded in the
  *same statement* (`record_progress` advances `progress_ply` and
  `heartbeat_at` together).

`iter_game_moves` is a refactor of the Phase 1 `analyse_game_moves`, which is now
a thin wrapper that collects it. The two share one code path, so the worker's
numbers matching the CLI's is structural rather than maintained by hand — verified
by the live run: the worker reproduces the frozen golden fixture's accuracy of
**87.9** exactly.

Positions are still searched **exactly once** each (n plies → n+1 searches): ply
`i+1`'s "before" is the analysis ply `i` already computed, carried forward.

### 2. Cancellation is cooperative, checked *between* plies

`should_cancel` reads the job's `status` **from the database**. The cancel
endpoint (C4) runs in the *API* process, so the database is the only channel — an
in-process flag in the worker could never see it.

The check runs before each ply is recorded, and again after the loop, so:

- a cancelled job gains no row for work it is abandoning;
- a job cannot be marked `done` moments after the user asked it to stop.

The terminal transitions (`complete_job`, `fail_job`) are themselves conditional
on the job still being `queued`/`running`. The final check and `complete_job` are
two separate statements, so a cancel can land between them; the conditional
update is what makes the cancel win instead of being overwritten by a late
`done` or `failed`.

A cancel is **not a rollback**. The plies already written are true partial
results and stay; the job becomes `cancelled`, and no report is written. There is
deliberately **no attempt to kill Stockfish mid-search from here** — that is the
hard timeout (B5), a different mechanism for a different problem. The API
documents cancel as "within one ply".

### 3. Report first, status second

The order is: analyse all plies → build the report → `save_report` → mark `done`.

If the process dies between those steps, the job is still `running` and the reaper
(B4) requeues it — correct, because a `running` job with a report is simply
re-run and overwritten. The reverse order would produce a `done` job with **no
report**, which is the one unrecoverable state. So the code only ever produces the
ordering whose failure mode can be recovered.

`run_once` guarantees *a claimed job always leaves `running`*: it becomes `done`,
`cancelled`, or `failed` — never stuck. Any exception is caught there and recorded
via `fail_job`, with the engine closed in a `finally` so a crash cannot leak a
Stockfish process.

## The durable cache (`position_eval`)

B1 defined *what* a cache is (`analyzer/cache.py`: the key, the depth rule, the
hit semantics). `server/position_cache.py` is the storage half.

It implements the line-aware hooks `_store_lines` / `_load_lines` rather than the
base `_store` / `_load`, because a durable backend needs the **board**: a score's
point of view is only recoverable from the side to move. The key construction, the
`depth >= requested` rule and the "deepest row wins" selection are all **inherited
unchanged** — there is exactly one definition of a cache hit and it is not in this
file.

| Concern | How |
|---|---|
| concurrent writes | `INSERT … ON CONFLICT DO UPDATE` — one statement, so two workers on the same opening cannot clobber each other |
| depth rule (durable half) | `WHERE excluded.depth >= position_eval.depth` — a late shallow re-search cannot downgrade a stored deep row |
| non-serialisable `Info` | projected to JSON: raw score + the side to move + PV as UCI. Rebuilt into a `PovScore` on read, so `to_mover_pov` sees what a fresh search produced |
| enabled? | `CACHE_DIR` unset → `NullPositionCache` (a real value, not `None`) |

Verified live: a depth-16 row answers a depth-12 request without the engine, a
depth-20 request misses, a Stockfish-16 row does not match Stockfish 19, and a
second job over the same game completes from the cache.

## `read_game` is not `usable_game`

python-chess returns an **empty game** for text that is not a PGN at all (it
treats the whole thing as a comment), rather than raising. So the worker checks
`mainline_moves()` is non-empty and fails the job with "the stored PGN contains no
moves". Without that guard the job would store an empty report and be marked
`done` — a finished analysis of nothing.

## Reaper (B4)

The worker runs `reap_stale_jobs` periodically (configured by
`REAPER_INTERVAL_SECONDS`/`JOB_TIMEOUT_SECONDS`). It keys off `heartbeat_at`,
not `started_at`, so a long-but-healthy analysis is not mistaken for a dead
worker. A stale job is requeued (up to `MAX_ATTEMPTS`), otherwise marked
`failed`. This covers **worker process death**, which no in-process timeout can.

## Hard timeout (B5)

The worker passes `JOB_TIMEOUT_SECONDS` as a **per-move** timeout to
`analyzer.engine.Engine.analyse()`. If a single position search exceeds the
timeout, the engine process is terminated and `EngineTimeoutError` is raised;
the job is marked `failed` and the worker **survives** (its loop continues).
B4 covers a dead worker process entirely; B5 kills a live-but-hung Stockfish
for the current job.

## Not here

- **Retries** — `run_once` fails a job on exception; it does not requeue for
  another attempt. That policy belongs with the reaper, where the attempt count
  is already tracked.
