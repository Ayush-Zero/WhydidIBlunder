# Testing

## Offline by default

`pytest` **never** launches Stockfish. The whole suite is pure logic plus frozen
fixtures, so it runs anywhere in about a second:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Anything that needs a real engine is opt-in — see *The opt-in live test* below.

## What the suite protects

| Layer | File | Guards against |
|---|---|---|
| Pure-logic units | `tests/test_pov.py`, `test_terminal.py`, `test_classification.py`, `test_accuracy.py`, `test_phase.py`, `test_tags.py` | the maths drifting (win%, thresholds, accuracy, POV, mate encoding) |
| Engine wiring | `tests/test_engine_reuse.py` | a re-search creeping back in (2n engine calls instead of n+1) and settings going hardcoded |
| Frozen fixture, self-consistency | `tests/test_report.py` | a report that no longer recomputes from its own evals |
| Frozen fixture, exact flags | `tests/test_golden_game.py` | a *silently* different verdict on the sample game |

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
