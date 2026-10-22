# Storage contract (SQLite, Phase 2)

`server/db.py` owns the schema. This document explains the *decisions* in it —
the part a reviewer should push on. The API and the worker are thin
readers/writers; almost everything hard about Phase 2 is decided here.

- **Migration:** `server/db.py:MIGRATIONS` (append only; never edit an applied one)
- **Initialise:** `init_db(connect(path))` — idempotent, safe on every process start
- **Schema version:** `SCHEMA_VERSION` / `schema_version(conn)`

## Connection settings

| Setting | Why |
|---|---|
| `PRAGMA journal_mode=WAL` | the API reads while a worker writes. Asserted by reading the pragma back, because a `:memory:` database silently falls back. |
| `PRAGMA foreign_keys=ON` | SQLite ships with FKs **off**; without this, `ON DELETE CASCADE` is decoration. |
| `PRAGMA busy_timeout` | a second worker blocks politely instead of raising `database is locked`. |
| `isolation_level=None` | autocommit. Transactions are managed explicitly (`BEGIN IMMEDIATE`, B2) because an *implicit* deferred transaction takes its write lock only after reading — exactly the race the job claim must not have. |

## Tables

| Table | Purpose |
|---|---|
| `jobs` | the analysis queue: one row per requested analysis, `queued → running → done`. |
| `position_eval` | the shared engine cache — the biggest cost saver, and the easiest thing to poison. |
| `moves` | one row per analysed ply, written incrementally so poll responses can show partial progress. |
| `game_reports` | the finished report, stored whole plus a denormalised summary for cheap listing. |

### `jobs`

Keyed on `id` (a UUID4 — unguessable by design, since there is no auth and
anyone holding an ID can fetch it).

`game_key` is **not** named `game_uuid` on purpose: games older than chess.com's
UUID rollout have no `uuid`, and the key falls back to `url` → `link` → a digest
(see "Game keys" below). Naming the column after the fallback would be a lie
about half the rows.

`pgn` is stored on the job so a retry or a re-queue never has to go back to
chess.com.

`heartbeat_at` (added by migration 2) is what the reaper keys off. It is
deliberately **not** derived from `started_at`: a large analysis may legitimately
run past the timeout, so *how long it has been running* cannot distinguish a slow
job from a dead worker. *When progress was last recorded* can.

### `position_eval` — the cache key

```
PRIMARY KEY (fen_key, engine_version, depth, multipv)
```

Three separate decisions live in that key:

1. **`fen_key` is the normalised FEN** — the halfmove clock is **included**, the
   fullmove counter is **stripped**. Including the clock is the safe call: the
   50-move rule is a real input to an engine's evaluation near the horizon, so
   two positions that differ only in the clock can legitimately eval differently.
   Stripping the fullmove counter is safe because nothing in evaluation depends
   on move number, and keeping it would fragment the cache every ply.
2. **`engine_version` is a key column, not part of `fen_key`.** Different
   Stockfish builds return different evals; mixing them silently corrupts the
   cache. Because it is part of the primary key, a new build simply stops
   matching old rows rather than colliding with them.
3. **The depth-≥ rule is a query, not a constraint.** A row analysed at depth 12
   can answer a request for depth 8 (deeper is strictly better); it cannot answer
   depth 20. That lookup is B1.

> **Column order note.** The plan writes the key as
> `(fen_key, depth, multipv, engine_version)`. This schema uses
> `(fen_key, engine_version, depth, multipv)` instead — same four columns, but
> `depth` now precedes `multipv`, which lets SQLite answer the "deepest row at or
> above the requested depth for this fen + engine" lookup from an index prefix.
> With the plan's order, every such lookup is a table scan.

### `moves`

`(job_id, ply)` rather than a surrogate, so a worker can insert plies as it goes.
That is what makes partial poll results (C3) free rather than a separate feature.

Columns mirror `docs/analyzer-schema.md` field-for-field, including the stored
`fen_before` / `fen_after` — those are what let B1 reuse each position's "after"
as the next move's "before" without re-parsing the PGN.

### `game_reports`

`PRIMARY KEY (game_key, user_color)`: the same game analysed from both sides is
two different reports, and that pair is exactly what the API dedupes on. Making
it the primary key turns "reuse an existing report if there is one" into an
`ON CONFLICT` instead of a lookup-then-insert race.

The summary columns (`accuracy`, `blunders`, …) are denormalised out of
`report_json` so a game list does not have to parse every report body.

## Game keys

`server/chesscom.py:game_key()`:

| Priority | Source | Key |
|---|---|---|
| 1 | `uuid` | `chesscom:<uuid>` |
| 2 | `url` | `chesscom-url:<url>` |
| 3 | `link` | `chesscom-url:<link>` |
| 4 | derived | `derived:<sha256[:32]>` |

The prefix makes the provenance of any key visible in the database, which
matters because URLs are built from it. Case 4 exists because an older daily game
can arrive with none of the three; a duplicate report is a worse outcome than an
opaque key.

## Deliberately not here yet

- **The durable position cache** — B1 defines the key and the depth rule
  (`analyzer/cache.py`), but the SQLite-backed subclass that writes to
  `position_eval` lands with B2/B3, which need a connection anyway. The key
  columns already match the table exactly.
- **The reaper and the hard timeout** — **B4 / B5**. `server/queue.py` owns the
  claim and the transitions; it does not yet sweep for stale jobs.
- **Anything FastAPI beyond the skeleton** — **A2 / C\***.
