# Analyzer report schema (v1)

The public output of `analyzer.analyze_game(pgn, color, *, engine, depth, multipv)`
is a JSON-serialisable `dict`. This document freezes that shape. Everything
downstream — the API (Phase 2), the database, the UI (Phase 3) — reads these
fields, so changes here are deliberate and version-bumped.

- **Schema version:** `1` (field `schema_version`)
- **Validator:** `analyzer.report.validate_report(report) -> list[str]`
  (empty list means valid)
- **Characterisation test:** `tests/test_report.py` against
  `tests/fixtures/immortal_game.report.json`

## Units and point-of-view (read this first)

This is the #1 source of bugs, so it is stated once per field group:

| Field group | Units | Point of view |
|---|---|---|
| `eval_before_cp`, `eval_after_cp` | centipawns (1 pawn = 100); mates encoded near ±10000 | the **mover** of that ply |
| `eval_before_win_pct`, `eval_after_win_pct` | win%-points (0–100) | the **mover** |
| `drop_win_pct` | win%-points (0–100); positive = worse for the mover | the **mover** |
| `mate_before`, `mate_after` | signed mate distance (moves), or `null` | the **mover** (positive = mover is mating) |
| `summary.accuracy` | 0–100 | `user_color` |

"mover" = the side that played the move being reported. A position *after* a move
has the **opponent** to move, so the pipeline converts it back to the mover's POV
with `score.pov(mover)` before computing anything.

## Top level

```jsonc
{
  "schema_version": 1,
  "engine":  { "name": "Stockfish 19", "depth": 12, "multipv": 4 },
  "game":    { ... },
  "moves":   [ { ... }, ... ],
  "summary": { ... }
}
```

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | currently `1` |
| `engine.name` | string | engine id string, e.g. `"Stockfish 19"` |
| `engine.depth` | int ≥ 1 | search depth used for every position |
| `engine.multipv` | int ≥ 1 | number of candidate lines requested |
| `game` | object | see below |
| `moves` | array | one entry per ply, in order |
| `summary` | object | see below |

## `game`

| Field | Type | Notes |
|---|---|---|
| `white` | string | non-empty |
| `black` | string | non-empty |
| `result` | string | PGN result, e.g. `"1-0"` (may be empty) |
| `date` | string | PGN date (may be empty) |
| `event` | string | may be empty |
| `eco` | string | may be empty |
| `link` | string | chess.com `Link` (falls back to `Site`); may be empty |
| `user_color` | `"white"` \| `"black"` | whose moves the summary covers |

## `moves[]` — one entry per half-move

| Field | Type | Notes |
|---|---|---|
| `ply` | int ≥ 1 | 1-based half-move index (ply 1 = White's 1st move) |
| `move_number` | int ≥ 1 | full-move number |
| `side` | `"white"` \| `"black"` | the mover |
| `san` | string | the move that was played, in SAN |
| `best_san` | string | engine's top move, in SAN |
| `pv_san` | string[] | up to 6 moves of the engine's best line (SAN) |
| `eval_before_cp` | int | evaluation before the move, mover's POV |
| `eval_after_cp` | int | evaluation after the move, mover's POV |
| `eval_before_win_pct` | number | `win_pct(eval_before_cp)`, 0–100 |
| `eval_after_win_pct` | number | `win_pct(eval_after_cp)`, 0–100 |
| `mate_before` | int \| null | mate distance (moves) before the move, mover's POV |
| `mate_after` | int \| null | mate distance (moves) after the move, mover's POV |
| `played_rank` | int ≥ 1 \| null | where the played move ranked among the MultiPV lines; `null` = not in the list |
| `drop_win_pct` | number | `win_pct(before) − win_pct(after)`; the classification input |
| `classification` | `"ok"` \| `"inaccuracy"` \| `"mistake"` \| `"blunder"` | see rules below |
| `tags` | string[] | informational, e.g. `"missed_mate"`, `"allowed_mate"` |
| `suppressed` | bool | true if a suppression rule forced `ok` |
| `suppress_reason` | string | `""`, `"forced"`, `"best move"`, or `"~= best"` |

### Classification rules (in precedence order)

1. **Forced move** (exactly one legal reply) → `ok`, no tags.
2. **Engine's best move** (rank 1) → `ok`, reason `"best move"`.
3. **`drop_win_pct ≤ 0.2`** (essentially equal to best) → `ok`, reason `"~= best"`.
4. Otherwise by `drop_win_pct`: `≥ 10` inaccuracy, `≥ 20` mistake, `≥ 30` blunder.

A suppressed move is **always** `"ok"` (enforced by the validator). Mate tags are
recorded independently and never suppress the label: `missed_mate` = the mover
had a forced mate and the played move loses it; `allowed_mate` = the played move
lets the opponent force mate.

## `summary`

| Field | Type | Notes |
|---|---|---|
| `user_color` | `"white"` \| `"black"` | matches `game.user_color` |
| `moves_analysed` | int ≥ 0 | number of plies played by `user_color` |
| `accuracy` | number 0–100 \| null | mean per-move accuracy over `user_color`'s moves; `null` if they played none |
| `blunders` | int ≥ 0 | count over `user_color`'s moves |
| `mistakes` | int ≥ 0 | idem |
| `inaccuracies` | int ≥ 0 | idem |
| `ok` | int ≥ 0 | idem |

`accuracy` is a Lichess-style expectation-based score and is **not** guaranteed to
match Chess.com's number.

## Guarantees the validator enforces

- `schema_version` is exact.
- Every declared field exists and has the right type.
- `encode` invariants: `side`/`user_color` ∈ {white, black};
  `classification` ∈ the four labels; `accuracy` ∈ [0, 100].
- **Internal consistency:** `summary` counts equal the moves classified that way,
  and `moves_analysed` equals the number of `user_color` moves.
- **Precedence invariant:** any move with `suppressed == true` is `"ok"`.

## Changing this schema

Bump `SCHEMA_VERSION` in `analyzer/report.py`, update this document and
`analyzer.report.validate_report`, and regenerate the fixture with
`scripts/gen_fixture.py`. The characterisation test will fail loudly if a change
is accidental.
