# Safety review (Phase 2 D5)

**Status:** signed off 2026-10-30.
**Scope:** the production code in `server/` and `analyzer/`, and the paths by
which request data, Chess.com data and environment configuration reach SQLite or
Stockfish.
**Not in scope:** Phase 5's real LLM layer, the Phase 3 frontend, and deployment
configuration (TLS, proxy limits, log retention — see
[`docs/ownership.md`](ownership.md)).

## The invariant

> A request chooses **which public game** to analyse, and nothing else. It cannot
> choose a command, an executable path, an engine option, a FEN, a depth, or an
> SQL fragment.

The review below is the evidence for that sentence. Where a risk is accepted
rather than eliminated, it is recorded as a residual instead of omitted.

## 1. Process execution: there is no shell

- The only production process launch is
  `chess.engine.SimpleEngine.popen_uci(...)` in `analyzer/engine.py`.
- The executable comes from `STOCKFISH_PATH` (operator configuration) and falls
  back to the literal name `stockfish`. No request field reaches it.
- python-chess starts the engine with `asyncio`'s `subprocess_exec` and an argv
  list — there is no command string and no shell. This is a property of the
  pinned library, checked in the installed `chess/engine.py`.
- There is no `os.system`, `os.popen`, or `shell=True` anywhere in `server/` or
  `analyzer/`. `tests/test_safety.py` enforces this with an AST scan, and proves
  the scanner detects both patterns.
- UCI configuration is exactly `Threads` and `Hash`, parsed as integers from
  environment variables. No UCI option name or value comes from a request.
- `subprocess.run([...])` is not banned by the tripwire: an argv list is safe.
  A newly added process launch is still a review event, which is the point.

## 2. Engine input: constructed positions only

- The worker sends `chess.Board` objects built by pushing the moves of a PGN
  (`server/worker.py` → `analyzer/pipeline.py`). Legal moves come from
  python-chess, not from the caller.
- The PGN comes from Chess.com, fetched for a validated username. Neither the API
  body nor the report endpoint accepts a PGN or FEN.
- `POST /api/explain` accepts a FEN-shaped string, but the Phase 2 stub never
  parses it, stores it, or sends it to an engine; it returns fixed text
  (`tests/test_api_explain.py`).
- Depth and MultiPV are read from server config when the job is created. The
  request model uses `extra="forbid"`, so `depth`, `multipv` or `threads` in the
  body is a `422` and no job/database write occurs
  (`tests/test_api_analyze.py::test_the_request_cannot_choose_analysis_cost`).

## 3. SQL: values are bound, identifiers are internal

- Every query that carries a value uses `?` parameters. The audit covered
  `server/db.py`, `server/queue.py` and `server/position_cache.py`.
- The only interpolated SQL is assembled from internal constants
  (`MOVE_COLUMNS` in `upsert_move`) or passed through
  `_ident()` — a strict `[A-Za-z_][A-Za-z0-9_]*` check used for `PRAGMA
  table_info`, which cannot take bound parameters. The busy-timeout value is cast
  to `int`.
- Migrations are static strings applied by version number; there is no
  request-controlled DDL.
- A report key containing SQL metacharacters is simply a key that does not
  exist: the request returns `404`, and the real report still reads afterwards
  (`tests/test_api_report.py::test_a_sql_metacharacter_key_is_just_a_key_that_is_missing`).

## 4. Upstream URLs and file paths

- The only URL built from request data is
  `{API_BASE}/player/{username}/games/archives`, and `username` must match
  `^[A-Za-z0-9_-]{1,50}$` before reaching the client. A rejected name makes no
  upstream request (`tests/test_api_games.py`).
- `game_key` is opaque: it is compared with keys derived from Chess.com data and
  stored/bound, never concatenated into a URL, shell command or filesystem path.
- HTTP cache files are named by the SHA-256 of the URL, so no username or key
  becomes part of a path. Writes are temp-file + `os.replace`, and an
  unreadable entry is a cache miss.
- **Residual (accepted for v1):** month archive URLs are taken verbatim from
  Chess.com's own archives response. That is an upstream-trust assumption, not a
  request-controlled value. If the client is ever pointed at a non-Chess.com
  source, add a prefix allowlist before fetching.

## 5. Edge-case policy

| Case | Behaviour | Evidence |
|---|---|---|
| Empty month | Chess.com's `404` means “no games”, not an error; it is returned as `[]` and is **not** cached | `test_chesscom.py::test_empty_month_404_yields_no_games`, `::test_404_responses_are_not_cached` |
| Bot game | Detected by `user_title == "bot"` or the `--bot` suffix; listed with `is_bot`, included by default, and explicitly analysable | `test_chesscom.py`, `test_api_games.py`, `test_api_analyze.py::test_analyze_accepts_a_bot_game` |
| From-position game | Detected via `type`, `initial_setup`, or a PGN `[FEN]` tag; excluded from the list by default (opt-in flag); analysis is refused with `422` because there is no ply 1 for the phase heuristic | `test_chesscom.py`, `test_api_games.py`, `test_api_analyze.py::test_from_position_games_are_refused` |
| Malformed FEN tag | Treated as not-from-position rather than raising | `test_chesscom.py::test_from_position_check_survives_malformed_fen_tag` |
| Missing PGN | `422`, not a queued job | `test_api_analyze.py::test_a_game_without_a_pgn_is_refused` |
| Missing `uuid` | Falls back to `url`, then `link`, then a digest of players/clock | `test_chesscom.py` |

## 6. Resource bounds and abuse

- `POST /api/analyze` is rate limited per client IP before any upstream lookup
  or engine work; `X-Forwarded-For` is honoured only from configured proxies.
- Analysis cost is fixed by server config (depth, MultiPV, Threads, Hash), not
  by the caller.
- A job is bounded by the per-move timeout (B5), cooperative cancellation (C4),
  and the heartbeat reaper (B4). A killed worker is covered by the reaper, which
  is verified end to end in `tests/test_e2e_reaper.py`.
- Job IDs are server-generated UUID4 capabilities; a client cannot choose or
  supply one (C8).
- **Residual:** the rate limiter is per-process and in memory; only analysis is
  limited. A shared store and a proxy-level global/body-size limit are required
  for a multi-process deployment (recorded in `docs/ownership.md`).

## 7. Secrets, errors and logs

- `/health` reports only status and schema version; it exposes no filesystem
  paths or configuration (`tests/test_app.py`).
- Request logging records method, path, status and duration; request bodies and
  headers are not logged. Paths can contain job IDs/game keys, which are not
  secrets but are capabilities — log access is therefore a deployment control.
- `.env` is gitignored. IDs carry no secret payload.
- API failures are short status + detail strings; FastAPI's default handler does
  not include tracebacks or environment values in the response.
- The explain stub deliberately does not echo user-controlled strings
  (`tests/test_api_explain.py`).
- **Residual:** a job's `error` is readable by anyone holding its bearer `job_id`
  and may contain operational detail (for example an engine failure message),
  but not configuration secrets.

## Sign-off

- [x] Every production process launch, SQL call site, engine option, and
      upstream URL construction was reviewed.
- [x] Shell-free shape is enforced by `tests/test_safety.py`.
- [x] Fixed analysis cost is enforced and tested.
- [x] SQL metacharacters are values, covered by a boundary test.
- [x] Empty-month, bot, from-position, missing-PGN and missing-`uuid` policies
      are implemented and tested.
- [x] Residual risks above are accepted for v1 and recorded rather than hidden.
- [x] No production code changes were required by this review.

Operational items from C8 (TLS, proxy limits, referrer policy, protecting
`.env`/database/logs) remain deployment requirements; they are not assertions
about this code and are tracked in [`docs/ownership.md`](ownership.md).
