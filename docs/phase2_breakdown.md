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
| **B1** | **Position cache (`fen_key`)** | Normalise FEN (strip fullmove counter; **include halfmove clock — decide explicitly**); **include engine version/build**; serve a cached row only when its **depth ≥ requested** | ✅ Done — `analyzer/cache.py`, `docs/cache.md`, `tests/test_cache.py`, `tests/test_cache_wiring.py` (SQLite backend pending in B) |
| **B2** | Atomic job claim | `BEGIN IMMEDIATE` + `PRAGMA busy_timeout`; `UPDATE … RETURNING id` sets `running`, `worker_id`, `started_at` | Concurrent-claim test: two workers never claim the same job; no `database is locked` |
| **B3** | Worker loop | Claim queued job → run `analyze_game`, persisting per-ply `moves` + progress; **check cancel between plies**; finalise `game_report` + job status | One job runs end to end; progress advances; status goes `queued → running → done` |
| **B4** | **Stale-job reaper** | Periodic sweep returns `running` jobs older than the hard timeout to `queued`/`failed` | Kill a worker mid-job → reaper requeues it → it completes |
| **B5** | Hard job timeout | Kill stuck Stockfish for the current job | A deliberately-hung analysis is killed; job marked failed/requeued, worker survives |

> B4 exists because a job timeout does **not** cover worker **process death**.
> Both are required, not one or the other.

---

## Milestone C — API surface (≈ 1 day)

| ID | Part | Endpoint / concern | Done when |
|----|------|--------------------|-----------|
| **C1** | Game list (proxy) | `GET /api/player/{username}/games` | Returns normalised list; served from cache on repeat |
| **C2** | Request analysis | `POST /api/analyze {username, game_uuid} → {job_id}` | Creates a `queued` job; reuses an existing report/job if already done |
| **C3** | Poll job | `GET /api/jobs/{id}` | Returns status + progress + **partial results** |
| **C4** | Cancel job | `POST /api/jobs/{id}/cancel` | Cooperative cancel flips a running job to `cancelled` within one ply |
| **C5** | Fetch report | `GET /api/games/{game_uuid}/report` | Returns the stored full report |
| **C6** | Explain **stub** | `POST /api/explain` | Route exists, returns a placeholder; **no LLM code** (that's Phase 5) |
| **C7** | Rate limiting | Per-IP limit (e.g. **5 analyses/hour**); trust `X-Forwarded-For` **only** from Caddy | 6th request in the window → `429`; spoofed XFF from a non-proxy is ignored |
| **C8** | Ownership / abuse model | No auth: **unguessable UUIDs** + rate limit; **never put secrets in IDs** | Written down (README/plan); IDs are UUID4; documented that anyone with an ID can fetch it |

---

## Milestone D — Verification & hardening (≈ ½–1 day)

| ID | Part | Deliverable | Done when |
|----|------|-------------|-----------|
| **D1** | **E2E happy path** | POST a game → poll → full report (this *is* the phase's done-criterion) | Scripted test passes against recorded chess.com + real Stockfish |
| **D2** | Cancel E2E | Cancel mid-flight | Job stops, reports `cancelled`, no orphaned Stockfish process |
| **D3** | Reaper E2E | Kill worker mid-job | Job requeues and completes; no job stuck `running` forever |
| **D4** | Cache correctness suite | Engine-version key, depth-≥ rule, FEN normalisation (halfmove clock), poisoning | All green; documented key definition |
| **D5** | Safety review | No user input to the shell — only constructed FENs/UCI options; empty-month / bot / from-position handled | Checklist signed off in the PR |

---

## Definition of Done for Phase 2

- [ ] **POST a game → poll → full report** works end to end. *(plan's done-criterion)*
- [ ] **Cancel mid-flight** works and leaves no orphaned engine. *(plan's done-criterion)*
- [ ] **Reaper verified**: kill a worker, job requeues.
- [ ] Cache **cannot poison itself**: engine-versioned + depth-aware + normalised key.
- [ ] Rate limit enforced; `X-Forwarded-For` trusted only from Caddy.
- [ ] No auth by design; unguessable UUIDs; **no secrets in IDs**.
- [ ] Runs as **1 worker, `Threads=1`, `Hash=256`**; scaling = more processes.

---

## Recommended execution order & parallelisation

```
A1 ─┬─ B1 ─ B2 ─ B3 ─┬─ C2 ─ C3 ─ C4 ─ D1 ─ D2 ─ D3
    │                 │
A2 ─┤                 └─ B5 ─ B4
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
2. ~~**B1** — the `fen_key` definition + tests~~ ✅ done; see `docs/cache.md`. The
   SQLite backend for it belongs with B2/B3 (it needs a connection anyway).
3. **B2 → B3** — atomic claim + worker loop (the concurrency core).
4. **B4 → B5** — the reaper and the hard timeout. Both are required: B4 covers
   worker *process death*, which a job timeout cannot.

> Milestone C needs B3 (there is nothing to poll until jobs run).

> Dependency note: Phase 2's worker calls the Phase 1 `analyze_game(pgn, color)
> -> dict`. Phase 1's **frozen output schema (A1)** and **pure-logic tests** are
> what let Phase 2 proceed without an engine in CI.
