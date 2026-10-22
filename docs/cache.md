# Position cache (Phase 2 B1)

`analyzer/cache.py` defines how a cached engine result is keyed and when a stored
row may answer a request. This document is about the two decisions a reviewer
should push on, because **a wrong cache key does not crash — it serves the wrong
evaluation forever.**

- **Implementation:** `analyzer/cache.py`
- **Tests:** `tests/test_cache.py` (key logic), `tests/test_cache_wiring.py` (in
  front of the engine)
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
- `store()` never lets a later shallower result shadow a deeper one already
  present.

## Enabled deliberately, not by default

`CACHE_DIR` unset means **no caching**. A cache is a correctness-bearing
optimisation; it should be switched on by intent (in `.env`) rather than
inherited from a fresh checkout. `report.default_cache()` returns `None` when
disabled, and `None` is also what the pipeline takes to mean "no cache" — the
disabled case is `NullPositionCache`, a real value, so "no cache" can be passed
around explicitly instead of being confused with "forgot to pass one".

## Backends

| Backend | Where | Notes |
|---|---|---|
| `MemoryPositionCache` | `analyzer/cache.py` | in-process; the default when enabled. Shares all key logic with the durable one. |
| `NullPositionCache` | `analyzer/cache.py` | "off", as a value. |
| SQLite-backed | **Phase 2 B** | writes to `position_eval` in `server/db.py`. The key columns already match: `PRIMARY KEY (fen_key, engine_version, depth, multipv)`. |

The SQLite storage table exists (A1) but nothing writes to it yet — B1 defines
*what* a cache is; wiring it to the database column is the remaining half of the
phase. Until then the durable cache is one SQLite subclass away, which is why the
key decisions were made to match the table exactly.
