# Position cache (Phase 2 B1)

`analyzer/cache.py` defines how a cached engine result is keyed and when a stored
row may answer a request. This document is about the two decisions a reviewer
should push on, because **a wrong cache key does not crash — it serves the wrong
evaluation forever.**

- **Implementation:** `analyzer/cache.py` (key, lookup semantics),
  `server/position_cache.py` (durable `position_eval` backend used by the worker)
- **Tests:** `tests/test_cache.py` (key logic), `tests/test_cache_wiring.py` (in
  front of the engine), `tests/test_position_cache.py` (the same key enforced via
  SQLite), `tests/test_db.py` (the table's primary key)
- **Key version:** `CACHE_KEY_VERSION` — bump when a stored row stops meaning
  what it used to; old rows are then ignored, never reinterpreted.

## The key

```
(fen_key, engine_version, depth, multipv)
```

### `fen_key` — the normalised position

The fullmove counter is **stripped**; the halfmove clock is **kept**. Stated as a
field mask in the code, not just in prose:

```python
CACHE_FEN_SCHEMA = "board turn castling ep halfmove"
```

Both choices are the *safe* direction, which is the only argument that matters
for a cache:

| Field | Decision | Why |
|---|---|---|
| fullmove counter | **drop** | nothing about a position's evaluation depends on the move number. Keeping it would fragment the cache once per ply — halving the hit rate for no benefit. |
| halfmove clock | **keep** | the 50-move rule is a real input to an evaluation near the horizon, so two positions differing only in the clock can legitimately be scored differently. A cache that ignores the clock can serve a draw-ish score for a position the engine would have scored as a win. |

A malformed FEN is normalised to itself rather than raising: nonsense input
should produce a *cache miss*, not an exception from the cache layer.

### `engine_version`

Stockfish's own id string (`"Stockfish 19"`), read from the **engine object that
answered** — never from a CLI constant.

Different builds return different evaluations. The failure mode of getting this
wrong is the nastiest one in the whole phase: the cache returns a *plausible*
number computed by a different engine, and nothing downstream can tell. Because
it is a separate key column rather than baked into `fen_key`, an old build's rows
survive inspection instead of becoming indistinguishable from the new ones.

### `depth` and `multipv`

`depth`: a stored row may answer a request for the **same or shallower** depth,
never a deeper one.

```python
# hit:   stored 20, requested 12  ->  (20, value)
# miss:  stored  8, requested 20  ->  None
```

Serving the shallow row would silently weaken the analysis while looking like a
cache win — the most dangerous kind of "optimisation".

`multipv`: must match **exactly**. MultiPV=1 and MultiPV=4 return different first
lines (with one line Stockfish reports the best move; with several it reports
each line's own evaluation), so one is not a prefix of the other.

## Lookup semantics

`lookup()` returns `(stored_depth, value)` or `None`:

- the deepest satisfying row wins, so an existing deep evaluation is preferred
  over one that merely clears the bar;
- the stored depth is returned so a caller can record how deep the hit really
  was — a report must not claim depth 12 when it reused a depth-20 row;
- stores never overwrite across depths: `depth` is part of the key, so a later
  shallower search is a **new row** that a deeper request ignores. Only a
  same-depth re-search replaces its own row (the durable backend upserts on the
  full key, so duplicate stores cannot create duplicates).

## Enabled deliberately, not by default

`CACHE_DIR` unset means **no caching**. A cache is a correctness-bearing
optimisation; it should be switched on by intent (in `.env`) rather than
inherited from a fresh checkout.

The two callers honour the switch differently:

| Caller | Disabled | Enabled |
|---|---|---|
| CLI / `report.analyze_game` | `default_cache()` returns `None` | `MemoryPositionCache` (one process) |
| Worker | `default_cache_for()` returns `NullPositionCache` | `SqlitePositionCache` (durable) |

`None` is what the pipeline takes to mean "no cache"; `NullPositionCache` is a
real value for callers that must pass *something*, so "off" is never confused
with "forgot to pass one". The worker's switch only enables the backend — it does
not choose a directory: rows live in `position_eval` in the job database.

## Backends

| Backend | Where | Notes |
|---|---|---|
| `MemoryPositionCache` | `analyzer/cache.py` | in-process; what the CLI uses when enabled. Shares all key logic with the durable one. |
| `SqlitePositionCache` | `server/position_cache.py` | durable; what the worker uses when enabled. `PRIMARY KEY (fen_key, engine_version, depth, multipv)` in `server/db.py`. |
| `NullPositionCache` | `analyzer/cache.py` | "off", as a value. |

## How it is verified

The four correctness properties each have a test that fails loudly if the key
changes:

| Property | Test |
|---|---|
| fullmove stripped / halfmove kept | `tests/test_cache.py`, and through SQLite in `tests/test_position_cache.py` |
| engine bump is a miss, not a wrong answer | `test_cache.py`, `test_position_cache.py` (old rows survive for inspection) |
| a shallow row cannot settle a deeper request, and the stored depth is reported | `test_cache.py`, `test_position_cache.py` |
| poisoning: duplicates upsert one row; a key-schema bump ignores old rows | `test_position_cache.py` |

Wiring — that the pipeline actually skips the engine on a hit and does not serve
it across a bump — is `tests/test_cache_wiring.py`. The durable backend is also
exercised end to end by `tests/test_worker.py` (a second job over the same game
completes from the cache).
