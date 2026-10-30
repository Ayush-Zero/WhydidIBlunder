# Phase 2 — API + Worker: Work Breakdown

> Companion to the project plan. Phase 2 turns the Phase 1 `analyzer` into a
> running **service**: FastAPI + a separate worker process + SQLite (WAL) +
> native Stockfish. Still **no frontend**.

## Guiding principles (why the work is split this way)

1. **Schema first, endpoints last.** The DB tables and the cache key are the
   foundation; every endpoint just reads/writes them. Lock the storage contract
   before building routes.
2. **The cache is the sneaky bug.** A wrong `fen_key` silently serves bad evals
   forever. It gets its own part, its own tests, and goes early.
3. **Concurrency is the risk.** No Redis — the job queue is raw SQLite. The
   atomic claim and the reaper are the two places correctness can quietly break.
4. **One worker, `Threads=1` first.** Prove single-worker correctness end to end
   before considering scale. Scaling later = more processes, no rewrite.
5. **Everything testable without the network.** Chess.com responses are recorded
   as fixtures; the engine can be faked for API tests.

---

## Milestone A — Foundations (≈ 1 day)

| ID | Part | Deliverable | Done when |
|----|------|-------------|-----------|
| **A1** | SQLite schema + migrations | Tables `jobs`, `position_eval`, `moves`, `game_reports`; WAL enabled; a migration/init path | ✅ Done — `server/db.py`, `tests/test_db.py`, `docs/storage.md` |
| **A2** | App skeleton + config | FastAPI app, settings from `.env`, structured logging, `/health` | ✅ Done — `server/app.py`, `server/logging_setup.py`, `server/main.py`, `tests/test_app.py` |
| **A3** | Chess.com client (server-side) | Fetch archives + a month's games; **cache responses**; handle **empty months (404)**, **bots**, **"from position" games**, and games with **no `uuid`** (fall back to `url`/`link` as the key) | ✅ Done — `server/chesscom.py`, `tests/test_chesscom.py` over recorded JSON |

---

## Milestone B — Engine, cache & queue (≈ 1–1.5 days)

| ID | Part | Deliverable | Done when |
|----|------|-------------|-----------|
| **B1** | **Position cache (`fen_key`)** | Normalise FEN (strip fullmove counter; **include halfmove clock — decide explicitly**); **include engine version/build**; serve a cached row only when its **depth ≥ requested** | ✅ Done — key logic in `analyzer/cache.py` (`docs/cache.md`); durable backend in `server/position_cache.py` (B3) |
| **B2** | Atomic job claim | `BEGIN IMMEDIATE` + `PRAGMA busy_timeout`; `UPDATE … RETURNING id` sets `running`, `worker_id`, `started_at` | ✅ Done — `server/queue.py`, `docs/queue.md`, `tests/test_queue.py` (migration 2 adds `heartbeat_at`) |
| **B3** | Worker loop | Claim queued job → run `analyze_game`, persisting per-ply `moves` + progress; **check cancel between plies**; finalise `game_report` + job status | ✅ Done — `server/worker.py`, `server/position_cache.py`, `server/worker_main.py`, `docs/worker.md`, `tests/test_worker.py` (verified live: 45 plies, accuracy 87.9) |
| **B4** | **Stale-job reaper** | Periodic sweep returns `running` jobs older than the hard timeout to `queued`/`failed` | ✅ Done — `server/queue.py` (`reap_stale_jobs`), integrated into `server/worker.py`, `tests/test_queue.py` (keys off `heartbeat_at`) |
| **B5** | Hard job timeout | Kill stuck Stockfish for the current job | ✅ Done — per-move timeout in `analyzer/engine.py`, propagated by worker (`server/worker.py`), job marked `failed`, worker survives |

> B4 exists because a job timeout does **not** cover worker **process death**.
> Both are required, not one or the other.

---

## Milestone C — API surface (≈ 1 day)

| ID | Part | Endpoint / concern | Done when |
|----|------|--------------------|-----------|
| **C1** | Game list (proxy) | `GET /api/player/{username}/games` | ✅ Done — `server/api.py`, `tests/test_api_games.py`; normalised list, bot/from-position policy, disk-cached. Contract in `docs/api.md` |
| **C2** | Request analysis | `POST /api/analyze {username, game_key} → {job_id}` | ✅ Done — `server/api.py`, `tests/test_api_analyze.py`; reuses a stored report or active job, 202/200. Contract in `docs/api.md` |
| **C3** | Poll job | `GET /api/jobs/{id}` | ✅ Done — `server/api.py`, `tests/test_api_jobs.py`; status, `progress_ply`, persisted partial moves, `error`. Contract in `docs/api.md` |
| **C4** | Cancel job | `POST /api/jobs/{id}/cancel` | ✅ Done — `server/api.py`, `tests/test_api_cancel.py`; queued/running → `cancelled` immediately, idempotent, partial moves kept. Contract in `docs/api.md` |
| **C5** | Fetch report | `GET /api/games/{game_key}/report?user_color=...` | ✅ Done — `server/api.py`, `tests/test_api_report.py`; stored report served verbatim for `(game_key, user_color)`, path-converter keys, 404/422/503. Contract in `docs/api.md` |
| **C6** | Explain **stub** | `POST /api/explain` | ✅ Done — `server/api.py`, `tests/test_api_explain.py`; accepts `{fen, played, best, pv, tags}`, returns `{explanation, stub: true}`. **No LLM code** (Phase 5). Contract in `docs/api.md` |
| **C7** | Rate limiting | Per-IP limit (**5 analyses/hour** by default); trust `X-Forwarded-For` **only** from Caddy | ✅ Done — `server/ratelimit.py`, `tests/test_ratelimit.py`, `tests/test_api_ratelimit.py`; sliding window, `429` + `Retry-After`, XFF ignored from a non-proxy. Contract in `docs/api.md` |
| **C8** | Ownership / abuse model | No auth: **unguessable UUIDs** + rate limit; **never put secrets in IDs** | ✅ Done — `docs/ownership.md`, linked from `README.md`; UUID4 job IDs, bearer-capability semantics, threat model, deployment rules, and "anyone with an ID can fetch it" documented |

---

## Milestone D — Verification & hardening (≈ ½–1 day)

| ID | Part | Deliverable | Done when |
|----|------|-------------|-----------|
| **D1** | **E2E happy path** | POST a game → poll → full report (this *is* the phase's done-criterion) | ✅ Done — `tests/test_e2e_service.py` (live): recorded Chess.com fixtures + real Stockfish drive C2 → worker → C3 → C5, assert the report is valid and served verbatim, and that a second request reuses it without requeueing |
| **D2** | Cancel E2E | Cancel mid-flight | ✅ Done — `tests/test_e2e_cancel.py` (live): a real worker is cancelled via C4 while `running`; job settles `cancelled`, partial plies remain, no report is written, and the Stockfish pid is gone |
| **D3** | Reaper E2E | Kill worker mid-job | ✅ Done — `tests/test_e2e_reaper.py` (offline): a real helper process is killed mid-analysis; the next worker's scheduled reaper requeues the stale `running` job, completes it on attempt 2, and leaves nothing `running` |
| **D4** | Cache correctness suite | Engine-version key, depth-≥ rule, FEN normalisation (halfmove clock), poisoning | All green; documented key definition |
| **D5** | Safety review | No user input to the shell — only constructed FENs/UCI options; empty-month / bot / from-position handled | Checklist signed off in the PR |

---

## Definition of Done for Phase 2

- [x] **POST a game → poll → full report** works end to end. *(plan's done-criterion)*
  Verified by `tests/test_e2e_service.py` against recorded Chess.com fixtures
  and a real Stockfish.
- [x] **Cancel mid-flight** works and leaves no orphaned engine. *(plan's done-criterion)*
  Verified by `tests/test_e2e_cancel.py` against a real Stockfish.
- [x] **Reaper verified**: kill a worker, job requeues.
  Verified by `tests/test_e2e_reaper.py` against a killed helper process.
- [ ] Cache **cannot poison itself**: engine-versioned + depth-aware + normalised key.
- [x] Rate limit enforced; `X-Forwarded-For` trusted only from Caddy.
- [x] No auth by design; unguessable UUIDs; **no secrets in IDs**. See
  `docs/ownership.md`.
- [ ] Runs as **1 worker, `Threads=1`, `Hash=256`**; scaling = more processes.

---

## Recommended execution order & parallelisation

```
A1 ─┬─ B1 ─ B2 ─ B3 ─┬─ C2 ─ C3 ─ C4 ─ D1 ─ D2 ─ D3
    │                │
A2 ─┤                └─ B5 ─ B4
    │
A3 ─┴─ C1 ──────────── C5 ─ C6 ─ C7 ─ C8
                                    └─ D4 ─ D5
```

- **Serial spine (highest risk first):** `A1 → B1 → B2 → B3 → C2/C3 → D1`.
- **Parallel-friendly:** `A3` (chess.com client) and the read-only endpoints
  (`C1`, `C5`) don't depend on the queue and can be built alongside B.
- **Independent:** `C6` (stub), `C7` (rate limit), `C8` (ownership docs).
- **Highest risk / do early:** `B1` (cache poisoning) and `B2` (claim) — both are
  silent-failure classes.

## Suggested first three steps

1. ~~**A1** — SQLite schema~~ ✅ done; see `docs/storage.md` for the key decisions.
   ~~**A2** — app skeleton~~ ✅ done. ~~**A3** — Chess.com client~~ ✅ done.
   **Milestone A is complete.**
2. ~~**B1** — the `fen_key` definition + tests~~ ✅ done; see `docs/cache.md`.
   ~~**B2** — the atomic claim~~ ✅ done; see `docs/queue.md`. 8 threads against
   one database file claim 40 jobs exactly once, and the harness is shown to
   *detect* a naive read-then-write claim.
   ~~**B3** — the worker loop~~ ✅ done; see `docs/worker.md`. The durable cache
   (`server/position_cache.py`) landed with it, since the worker has the
   connection.
3. ~~**B4** — the stale-job reaper~~ ✅ done; see `docs/queue.md`/`docs/worker.md`.
   ~~**B5** — the hard timeout~~ ✅ done. B4 covers worker *process death*,
   which a job timeout cannot.
4. ~~**C1** — the game list~~ ✅ done; see `docs/api.md`.
   ~~**C2** — request analysis~~ ✅ done, including the active-job dedupe
   (migration 3). ~~**C3** — poll~~ ✅ done. ~~**C4** — cancel~~ ✅ done.
   ~~**C5** — report~~ ✅ done. ~~**C6** — explain stub~~ ✅ done.
   ~~**C7** — rate limiting~~ ✅ done. ~~**C8** — ownership / abuse docs~~ ✅
   done; see `docs/ownership.md`. ~~**D1** — end-to-end happy path~~ ✅ done;
   see `tests/test_e2e_service.py`. ~~**D2** — cancel E2E~~ ✅ done; see
   `tests/test_e2e_cancel.py`. ~~**D3** — reaper E2E~~ ✅ done; see
   `tests/test_e2e_reaper.py`. **D4** (cache correctness) is next.

> Milestone C needs B3 (there is nothing to poll until jobs run).

> Dependency note: Phase 2's worker calls the Phase 1 `analyze_game(pgn, color)
> -> dict`. Phase 1's **frozen output schema (A1)** and **pure-logic tests** are
> what let Phase 2 proceed without an engine in CI.
