# API contract

The API is versioned by its URL prefix (`/api`) and documented live at
`/api/docs` (OpenAPI at `/api/openapi.json`). This file records the decisions
behind each endpoint; the OpenAPI schema is the field-by-field source of truth.

> **Status:** C1 (game list), C2 (request analysis), C3 (poll), C4 (cancel),
> C5 (report), C6 (explain stub) and C7 (rate limiting) are implemented. C8
> (ownership / abuse model) is documented in
> [`docs/ownership.md`](ownership.md). Milestone C is complete.
>
> **Phase 3:** complete. A1 (positions and phase labels), D1 (server-side
> try-again grading), D2 (try-again UI), D3 (component/integration harness) and
> D4 (Playwright browser coverage) are implemented. D5 records the final
> ownership, safety, CORS and deployment notes.

---

## C1 — `GET /api/player/{username}/games`

Lists a player's recent Chess.com games, newest first, normalised for the
browser. Served through the Chess.com client's on-disk cache, so a repeat call
is a disk read rather than a second trip upstream.

### Path parameter

| Name | Rules |
|---|---|
| `username` | `[A-Za-z0-9_-]{1,50}`, case-insensitive |

The pattern is enforced *before* any upstream request. Chess.com usernames
cannot contain `/`, so constraining the parameter keeps a crafted name from
being interpolated into a different upstream URL.

### Query parameters

| Name | Default | Rules | Why |
|---|---|---|---|
| `months` | `3` | 1–120 | Bounds how many monthly archives a first request walks. |
| `max_games` | `50` | 1–500 | Bounds the response; the client stops as soon as it has this many. |
| `time_class` | all | `bullet` / `blitz` / `rapid` / `daily` | The UI's filter. |
| `include_bots` | `true` | bool | A bot game is still a game you played, but its rating is meaningless. |
| `include_from_position` | `false` | bool | Such games have no ply 1, which corrupts the ply-based phase labels. |

The defaults match the client's (`server/chesscom.py`), so the endpoint cannot
silently disagree with the library underneath it.

### Response — `200`

```json
{
  "username": "beast-ash",
  "count": 2,
  "games": [
    {
      "game_key": "chesscom:7515eb15-befd-11f1-8dbb-feb0c301000f",
      "url": "https://www.chess.com/game/live/184741950578",
      "white": {"username": "BEAST-ASH", "rating": 1171, "result": "win", "title": null},
      "black": {"username": "Gopinath_27", "rating": 1146, "result": "checkmated", "title": null},
      "result": "1-0",
      "time_class": "rapid",
      "time_control": "600",
      "rated": true,
      "rules": "chess",
      "end_time": 1791013370,
      "is_bot": false,
      "from_position": false,
      "user_color": "white"
    }
  ]
}
```

Field notes:

- **`game_key` is opaque.** It is the A3 fallback chain (`uuid` → `url` →
  `link` → digest), prefixed with its provenance. Older games have no `uuid`,
  so this is the only identifier that is always present. Callers must not parse
  it.
- **`user_color`** is which side the requested username played, matched
  case-insensitively. It is `null` only if an upstream game omits that player —
  a defensive value, not a normal case.
- **`result`** is derived from the two per-player results. Chess.com does not
  put `1-0`/`0-1` on the game object; an unfinished game is `null` rather than
  a guessed draw.
- **`is_bot` / `from_position`** are included even though the default policy
  filters from-position games out, so a caller that opts in can label them.
- **No PGN.** The list is fetched on every page load; the PGN is needed only
  when analysis is requested (C2), which refetches it through the same cache.
  Keeping it out keeps the payload proportional to the number of games, not
  the number of moves.

### Errors

| Status | When |
|---|---|
| `404` | Chess.com has never heard of the player (`PlayerNotFound`). |
| `422` | The username or a query parameter fails validation. |
| `502` | Chess.com failed or rate-limited us (`ChessComError`). A bad gateway, not a bug in this service. |

A player who exists but has no games is **not** an error: the response is
`200` with `count: 0`.

### Caching

The endpoint inherits A3's response cache:

- archives list — 10-minute TTL (it grows);
- a month — 1-hour TTL (past months are immutable, the current one is not);
- 404s are never cached (a transient gap must not become permanent).

`CHESSCOM_CACHE_DIR` enables it; unset means every call goes upstream. The
tests assert the cache path with a fixed clock and a mock transport, so they
never touch the network or wait.

---

## C2 — `POST /api/analyze`

Turns one game from C1's list into one queued job. The body is deliberately
small: **the server refetches the PGN from Chess.com** rather than accepting one
from the caller, so a request cannot submit arbitrary content for analysis.

### Body

| Field | Default | Rules | Why |
|---|---|---|---|
| `username` | required | `[A-Za-z0-9_-]{1,50}` | Whose perspective to analyse. |
| `game_key` | required | 1–256 chars, opaque | From C1; never parsed. |
| `months` | `3` | 1–120 | How far back to search; must cover the range the list used. |

`extra="forbid"`: a typo such as `game_uuid` is a `422`, not a silently ignored
field.

### Response — `202` (new job) or `200` (reused)

```json
{
  "job_id": "6f1c0a52-...",
  "status": "queued",
  "reused": false,
  "game_key": "chesscom:7515eb15-...",
  "user_color": "white"
}
```

`status` is `queued`, `running` or `done`. `job_id` is `null` only in the
defensive case where a stored report outlived its job row
(`game_reports.job_id` is `ON DELETE SET NULL`); the report is still fetchable
by `(game_key, user_color)` in C5.

### Precedence

1. **A stored report wins** → `200`, `status: "done"`, `reused: true`.
2. **An active job wins** → `200`, the job's `queued`/`running` status,
   `reused: true`.
3. **Otherwise a new job** → `202`, `status: "queued"`, `reused: false`.

The race between the lookup and the insert is closed by the partial unique index
from migration 3: at most one `queued`/`running` job per `(game_key,
user_color)`. The loser of that race returns the winner's job.

Depth and MultiPV come from server config (`ANALYSIS_DEPTH`,
`ANALYSIS_MULTIPV`), never from the request — `depth=40` from a caller would be
a trivial denial-of-service.

### Refusals

| Status | When |
|---|---|
| `404` | Unknown player, or the game is not in the searched months. |
| `422` | No PGN, a from-position game, a username that did not play the game, or an invalid body. |
| `502` | Chess.com failed or rate-limited us. |

**From-position games are refused even though C1 can list them.** They have no
ply 1, so the ply-based phase heuristic would be silently wrong; listing them is
harmless, analysing them is not.

---

## C3 — `GET /api/jobs/{job_id}`

Polls one job. Returns its state and the part of the analysis that has actually
been persisted. This is what makes a long job observable without pretending the
unfinished part exists.

### Response — `200`

```json
{
  "job_id": "6f1c0a52-...",
  "status": "running",
  "game_key": "chesscom:7515eb15-...",
  "user_color": "white",
  "progress_ply": 12,
  "moves": [
    {
      "ply": 1,
      "move_number": 1,
      "side": "white",
      "san": "e4",
      "best_san": "e4",
      "pv_san": ["e4", "e5", "Nf3"],
      "eval_before_cp": 30,
      "eval_after_cp": 28,
      "eval_before_win_pct": 54.2,
      "eval_after_win_pct": 53.9,
      "mate_before": null,
      "mate_after": null,
      "played_rank": 1,
      "drop_win_pct": 0.3,
      "classification": "ok",
      "tags": [],
      "suppressed": true,
      "suppress_reason": "best move"
    }
  ],
  "error": null
}
```

| Status | Meaning |
|---|---|
| `queued` | Waiting for a worker; `moves` is empty. |
| `running` | A worker holds it; `moves` is the persisted prefix. |
| `done` | Finished; the full report is available in C5. |
| `failed` | Stopped with an `error`; partial moves remain. |
| `cancelled` | Stopped by C4; partial moves remain. |

Field notes:

- **`moves` is in ply order and uses the same shape as a report move.** The
  storage row's `fen_before`/`fen_after` are deliberately omitted: they are
  storage detail, and this response is polled repeatedly.
- **`progress_ply` is at least `len(moves)`.** A crash can persist a move before
  the worker advances its progress checkpoint, so the API reports the truthful
  persisted prefix rather than the stale counter.
- **Partial results survive `failed` and `cancelled`.** They are what the worker
  actually wrote; deleting them because the job did not finish would throw away
  real analysis.
- **`error` carries the worker's reason** — the failure for `failed`, or the
  reaper's explanation while a requeued job waits. It is `None` for `done` and
  `cancelled`.
- **Polling never mutates the job.** It is a read-only view; a status check that
  changes state would be a second scheduler.

### Errors

| Status | When |
|---|---|
| `404` | No job with that id. |
| `422` | The id fails path validation (empty, or longer than 64 chars). |
| `503` | The database has not been initialised yet (no worker has run). |

There is no auth (C8): anyone holding the UUID4 can poll it. The id is
unguessable and carries no secret. See
[`docs/ownership.md`](ownership.md) for the full capability model.

---

## C4 — `POST /api/jobs/{job_id}/cancel`

Asks a queued or running job to stop. The status row flips immediately; the
worker checks it between plies, so the engine may still be finishing the ply it
had already started. "Cancelled within one ply" is the contract.

### Response — `200`

```json
{
  "job_id": "6f1c0a52-...",
  "status": "cancelled",
  "cancelled": true
}
```

| Field | Meaning |
|---|---|
| `status` | The job's status **after** the request. |
| `cancelled` | `true` only if *this call* moved a queued/running job to `cancelled`. |

Field notes:

- **Idempotent.** A second click, or a cancel that arrives after the job
  finished, returns `200` with the terminal status and `cancelled: false`. There
  is no `409`: the caller learns what actually happened, and a late request does
  not turn into an error.
- **A cancel is not a rollback.** Plies already written stay; C3 keeps returning
  them. A cancel that lands before the analysis finishes writes no report; one
  that arrives after the report was saved leaves that complete report available
  to C5.
- **A queued job never starts.** The status changes before any worker can claim
  it, so the cancel is not a race against a worker that has not begun.
- **Cancel wins a late race.** `complete_job`/`fail_job` only transition a job
  that is still queued/running, so a worker finishing or failing a moment later
  cannot flip a cancelled job back to `done`/`failed`.
- **The endpoint does not kill Stockfish.** The current search is bounded by the
  per-move timeout; cancel takes effect at the next ply boundary. Killing a live
  engine mid-search is the hard-timeout mechanism (B5), not this one.

### Errors

| Status | When |
|---|---|
| `404` | No job with that id. |
| `422` | The id fails path validation (empty, or longer than 64 chars). |
| `503` | The database has not been initialised yet. |

There is no auth (C8): anyone holding the UUID4 can cancel it. The id is
unguessable and carries no secret. See
[`docs/ownership.md`](ownership.md) for the full capability model.

---

## C5 — `GET /api/games/{game_key}/report?user_color=...`

Returns the stored analysis for one game and one side. It is a pure read: no
engine, no Chess.com fetch, no reshaping. The body is the frozen Phase 1 report
described in [`analyzer-schema.md`](analyzer-schema.md), exactly as the worker
wrote it to `game_reports.report_json`.

### Response — `200`

```json
{
  "schema_version": 1,
  "game": { "white": { "...": "..." }, "black": { "...": "..." } },
  "engine": { "name": "Stockfish 17", "depth": 12, "multipv": 4 },
  "moves": [ { "ply": 1, "san": "e4", "...": "..." } ],
  "summary": { "moves_analysed": 42, "accuracy": 87.9, "...": "..." }
}
```

Field notes:

- **`user_color` is required.** A game can be analysed from both sides, and
  `(game_key, user_color)` is the storage key. There is no default: defaulting
  would silently return the other player's report.
- **The body is served verbatim.** The API does not re-serialise the report, so
  it cannot drift from the frozen schema by adding or dropping fields.
- **`game_key` is opaque and may contain `:` and `/`.** A3 can fall back to
  `chesscom-url:https://...`, so the route uses a path converter. Clients should
  URL-encode reserved characters such as `?`, `#` and `%`.
- **The report does not need its job.** `game_reports.job_id` is
  `ON DELETE SET NULL`; a report that outlived its job row is still served.

### Errors

| Status | When |
|---|---|
| `404` | No report for this `(game_key, user_color)`, including when only the other colour has one. |
| `422` | Missing or invalid `user_color`, or an over-long key. |
| `503` | The database has not been initialised yet. |

There is no auth (C8): anyone who knows the key and colour can fetch the report.
Neither is a secret. See [`docs/ownership.md`](ownership.md) for the full
capability model.

---

## Phase 3 A1 — `GET /api/games/{game_key}/positions?user_color=...`

Derives the board state for every ply of a stored report. The report stores SAN
and evaluations, not FENs; this read-only endpoint replays the SANs from the
standard starting position and returns both FENs plus the existing phase hint.
It is pure: no engine, no Chess.com fetch, and no reshaping of the stored
report.

### Response — `200`

```json
{
  "game_key": "chesscom:game-1",
  "user_color": "white",
  "starting_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "positions": [
    {
      "ply": 1,
      "fen_before": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
      "fen_after": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
      "phase": "opening"
    }
  ]
}
```

Field notes:

- **One entry per ply**, in report order. `positions[i].ply == i + 1`.
- **`fen_before` is the position in which the mover chose the move.**
  `fen_after` is after the move is pushed.
- **`phase` describes `fen_before`**, using
  `analyzer.phase.detect_phase(board, ply - 1)`. It is a UI hint, never a
  classification input.
- **The standard start is guaranteed by C2.** Games beginning from a custom
  position are refused there because their ply numbering would corrupt phase
  labels; this endpoint never needs a starting FEN.
- **No schema change.** The C5 report remains the frozen v1 body, served
  verbatim. The frontend fetches this view alongside it.
- **`game_key` is opaque and may contain `:` and `/`**, so the route uses a path
  converter. URL-encode reserved characters such as `?`, `#` and `%`.

### Errors

| Status | When |
|---|---|
| `404` | No report for this `(game_key, user_color)`, including when only the other colour has one. |
| `409` | The stored report exists but cannot be replayed (invalid JSON, missing moves, wrong side, illegal SAN, or a gap in ply numbers). |
| `422` | Missing or invalid `user_color`, or an over-long key. |
| `503` | The database has not been initialised yet. |

This is a read of the same public game data as C5. There is no auth and no new
secret; anyone who knows the key and colour can derive the positions.

---

## Phase 3 D1 — `POST /api/games/{game_key}/try`

Grades one candidate move against a stored report. The server reconstructs the
position from the report's SANs, validates the candidate with python-chess, and
searches **only the resulting position** at the report's stored depth and
MultiPV. The report supplies the position before the move, its win probability
and the best line; server configuration supplies the classification thresholds.
A request cannot send a FEN, a depth, an engine path or a threshold.

### Request

```json
{
  "user_color": "white",
  "ply": 24,
  "move_san": "Nf3"
}
```

`user_color` selects the stored report, `ply` selects one of that side's moves,
and `move_san` is any **legal** SAN move in the position before that ply. The
body is `extra="forbid"`, so an invented `fen` or `depth` field is a `422`.

### Response — `200`

```json
{
  "legal": true,
  "accepted": false,
  "classification": "mistake",
  "drop_win_pct": 23.4,
  "eval_after_cp": -120,
  "best_san": "d4",
  "pv_san": ["d4", "d5", "exd5"],
  "threshold_win_pct": 10.0,
  "reason": "23.4 win%-points worse than the position before the move"
}
```

Field notes:

- **`legal` is always `true` on a `200`.** An illegal or ambiguous candidate is
  a `422`, never a result with `legal: false`.
- **The acceptance ladder is the analyzer's existing precedence.** A forced
  move passes; the report's exact best move passes; a non-best move whose drop is
  at most `SUPPRESS_WIN_PTS` (default `0.2`) passes; otherwise the configured
  inaccuracy/mistake/blunder thresholds decide. `accepted` is true exactly when
  the resulting `classification` is `ok`.
- **`drop_win_pct` is `report.eval_before_win_pct − win_pct(eval_after_cp)`**, in
  win%-points. Both sides of that subtraction are from the **mover's** point of
  view. `eval_after_cp` is the mover's after-eval, not White's.
- **`threshold_win_pct` is the inaccuracy threshold**, the boundary between
  `ok`/accepted and `inaccuracy`/rejected. Other thresholds are configured but
  not repeated in the response.
- **The best line comes from the stored report**, not a second search of the
  before-position. That is deliberate: it keeps try-again to one new search and
  keeps the comparison consistent with the report. Engine drift between the
  report and the current binary is accepted and documented in the breakdown.
- **The search uses the report's depth/MultiPV**, even if the server's current
  analysis config has changed. The durable `position_eval` cache is consulted
  before the search and filled after it.
- **The after-eval is cached under the current engine id**, so a binary upgrade
  cannot silently reuse an old score.

### Errors

| Status | When |
|---|---|
| `404` | No report for this `(game_key, user_color)`. |
| `409` | The stored report exists but cannot supply a baseline (invalid JSON, unplayable SAN, missing best line, or missing engine depth/MultiPV). |
| `422` | Bad `ply`, the ply belongs to the other colour, an illegal/ambiguous candidate SAN, or an invalid body. |
| `429` | The separate try-again per-IP limit was exceeded. Includes `Retry-After` and the `X-RateLimit-*` headers. |
| `503` | The database is not ready, another try evaluation holds the single engine slot, the engine timed out, or the engine failed. |

### Engine lifecycle and limits

The API process, not the worker, owns this engine call. To keep it from becoming
an unbounded second worker:

- **One concurrent try evaluation per API process.** The slot is non-blocking;
  a second request gets a truthful `503` rather than queuing behind an unbounded
  search.
- **A hard timeout** from `TRY_TIMEOUT_SECONDS` (default `20`). A timed-out
  search closes the engine and returns `503`.
- **A separate per-IP bucket** from `RATE_LIMIT_TRY_PER_HOUR` (default `60`).
  Only requests that pass report/ply/SAN validation consume it; malformed bodies
  never reach the endpoint, and illegal moves or unknown reports spend no engine
  time. See C7 below for the shared headers and proxy rules.
- **CPU contention with the worker is expected.** The try slot limits *this API
  process*; it cannot reserve CPU away from another process on the host.

This is the same public game data as C5/A1. There is no auth and no new secret;
anyone who knows the key and colour can grade candidates against that report.

---

## C6 — `POST /api/explain` (stub)

Returns a fixed placeholder. The route exists so the frontend can wire the call
now; the LLM layer is Phase 5. There are no provider keys, prompts or network
calls here.

### Request

```json
{
  "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
  "played": "e4",
  "best": "d4",
  "pv": ["d4", "d5", "c4"],
  "tags": ["hanging_piece"]
}
```

- `fen`, `played` and `best` are required; `pv` and `tags` are optional (at most
  16 entries each).
- `extra="forbid"`: a typo like `pgn` is a `422`, not a silently ignored fact.
- This is the Phase 5 LLM input shape (`{fen, played, best, pv, tags}`), so the
  frontend does not have to change when the real explanation lands.

### Response — `200`

```json
{
  "explanation": "Explanations are not available yet. This is a placeholder ...",
  "stub": true
}
```

- **`stub: true`** lets the UI label the text honestly; it is not an explanation.
- **The text is fixed.** The stub does not echo user-controlled strings, so a
  crafted SAN or tag cannot be reflected into the page.
- **No database, no Chess.com, no engine.** The stub works before a worker has
  migrated the database, and it does not read the report.
- **Errors:** `422` for an invalid body, `405` for `GET`. There is no `404`/`503`
  path here.

---

## C7 — rate limiting on `POST /api/analyze` and `POST .../try`

`POST /api/analyze` is limited to **5 requests per client IP per hour** by
default. `POST /api/games/{game_key}/try` has its **own** bucket, default **60**
per client IP per hour; the two limits do not share counters. The request that
would exceed its bucket is a `429` with `Retry-After`.

The analysis limiter runs before the Chess.com lookup, so an over-limit caller
cannot spend upstream requests or Stockfish time. The try limiter runs after the
cheap report/ply/SAN validation, so illegal candidates and unknown reports do
not consume the try bucket and still cannot reach the engine.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `RATE_LIMIT_ANALYSES_PER_HOUR` | `5` | Analysis requests per IP per hour. `0` disables. |
| `RATE_LIMIT_TRY_PER_HOUR` | `60` | Try-again requests per IP per hour. `0` disables. |
| `TRUSTED_PROXY_IPS` | `127.0.0.1,::1` | Peers whose `X-Forwarded-For` is trusted. |

The window is sliding, not a fixed hourly bucket: a request expires exactly one
hour after it was made. The counters are in memory and per API process, and the
map is capped at 10,000 clients (evicting an old client can reset its count),
and an API restart starts with empty counters. v1 runs one API process; if the
API is scaled to multiple workers, each gets its own budget, so set the limit
with that in mind or move the counters into shared storage first.

### Response headers

Every allowed `200`/`202` response carries:

| Header | Meaning |
|---|---|
| `X-RateLimit-Limit` | The configured limit. |
| `X-RateLimit-Remaining` | Requests left in the current window. |
| `X-RateLimit-Reset` | Seconds until the oldest request expires. |

A denied request is `429` with `Retry-After` (seconds) and the same
`X-RateLimit-*` headers. The analysis detail is:

```json
{"detail": "rate limit exceeded; try again later"}
```

The try-again detail is:

```json
{"detail": "try-again rate limit exceeded; try again later"}
```

### What counts

Every analyze request with a valid body counts, including one that reuses a
stored report, returns `404` for an unknown game, or fails upstream. Those all
consume API capacity, and counting them keeps the rule simple. A body rejected
by validation (`422`) is not counted: FastAPI rejects it before the limiter
runs.

For try-again, a request counts only after the report exists, the ply belongs to
the requested colour, and the candidate SAN is legal. Unknown reports, wrong-side
plies and illegal candidates are `4xx` before the limiter and spend no engine
time. A valid try that finds the engine busy or times out still consumed its
bucket, because it is a genuine engine-bound request.

The game list, polling, cancel, report, positions, explain and `/health` are
never counted.

### X-Forwarded-For

The header is trusted **only** when the immediate peer is in
`TRUSTED_PROXY_IPS`. A direct caller cannot spoof an IP by sending the header.
When the peer is trusted, the **rightmost** entry is used, because Caddy appends
the address it actually saw; the leftmost entry is client-controlled. If the
rightmost entry is malformed, the peer address is used rather than an earlier
spoofable entry.

The default trusts loopback, which covers Caddy on the same host. In Docker,
set `TRUSTED_PROXY_IPS` to Caddy's network (for example `172.16.0.0/12`). Do
not use a range that also contains clients: that would let them be treated as
proxies. If the API is behind Caddy but `TRUSTED_PROXY_IPS` is left at loopback,
all callers share Caddy's bucket, which over-limits rather than bypasses.

---

## Not here yet

No Phase 3 endpoint remains. Milestone C, A1 and D1-D4 are complete; D5's
ownership, safety, CORS and deployment notes are in
[`docs/ownership.md`](ownership.md) and
[`docs/safety-review.md`](safety-review.md). The browser never sends a FEN,
depth, engine path or provider key; the server reconstructs every position.

The breakdown writes `game_uuid` for C2/C5; the identifier is **`game_key`**
for the reason above — not every game has a `uuid`, and the database is keyed on
the fallback chain.
