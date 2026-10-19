# Phase 1 — Analyzer Core: Work Breakdown

> Phase 1 turns the **Phase 0 prototype** into a hardened, tested,
> dependency-free `analyzer/` package with a **frozen public API**. No web, no
> DB.

## Guiding principles (why the work is split this way)

1. **Pure logic first, engine/IO last.** Math and rules can be tested in
   milliseconds, offline, in parallel. Engine code is slow, stateful, and can't
   be parallelised on one binary. Get the cheap wins out of the way first.
2. **One part = one testable deliverable.** Each part is ≤ ~half a day and has a
   single, observable "done when". No part depends on a later part.
3. **Freeze the contract early.** The `analyze_game()` output schema is the
   interface for Phases 2–3; locking it first prevents rework.
4. **Deterministic offline tests are the default.** Anything that needs a live
   Stockfish process is opt-in and only checks plumbing.
5. **Protect the refactor.** Phase 0 already works. Before changing it, capture
   its current behaviour so refactors are provably behaviour-preserving.

---

## Milestone A — Lock the contract (≈ ½ day)

| ID | Part | Deliverable | Done when |
|----|------|-------------|-----------|
| **A1** | Public API + output schema | `analyze_game(pgn, color, *, depth, multipv) -> dict`; a documented, JSON-serialisable schema (fields, **units**, **POV** per field) | Schema doc exists; a sample output validates against it; `analyzer` is importable as a package |
| **A2** | Phase 0 characterisation baseline | A frozen snapshot of current output for `samples/immortal_game.pgn` (flags + evals) committed as a test fixture | A test replays the sample and asserts the snapshot is unchanged; the baseline is the safety net for Milestones B–C |

**Why A2 first:** it is the cheapest possible regression net. Every subsequent
refactor can be verified in one command.

---

## Milestone B — Pure-logic units, offline TDD (≈ 1 day) — ✅ done

All of these run with **no engine** and are unit-testable in isolation. They can
be built in parallel by separate people (or separate sittings).

| ID | Part | What it is | Done when |
|----|------|-----------|-----------|
| **B1** | POV normalisation | A single `to_mover_pov(score, mover)` entry point; all scores normalised once, at intake | Tests prove identical results whichever side is to move; no default-POV read remains in the code |
| **B2** | Terminal & mate encoding | Never evaluate game-over positions (delivered mate = max, else draw); mate-distance encoding (`10000 - mate_in`) | Tests cover: delivered checkmate, being mated, stalemate, repetition; assert M1 ≠ M3 |
| **B3** | Classification + suppression + precedence | One function implementing the precedence ladder (forced → best/≤0.2 → thresholds); thresholds from config | Table-driven tests cover **every** precedence branch and each threshold boundary (9.9/10/19.9/20/29.9/30) |
| **B4** | Accuracy metric | `acc = clamp(103.1668·e^(−0.04354·winDiff) − 3.1669, 0, 100)`, winDiff in **win%-points** | Boundary tests (0, 100, mid) + game-mean test; units documented |
| **B5** | Phase detection | Opening / middlegame / endgame from ply + material heuristics | Crafted positions map to expected phase; no engine needed |
| **B6** | Rule-based tags | `hanging_piece`, `missed_capture`, `fork`, `pin` (plus existing `missed_mate`, `allowed_mate`) | Each tag is a pure function with a true **and** a false test case |

> **Rule tags are wired (C2).** The four rule tags (`hanging_piece`,
> `missed_capture`, `fork`, `pin`) live in `analyzer/tags.py` and are now
> surfaced by `analyse_game_moves`. They annotate a **flagged** move only
> (classification ≠ `ok`): the predicates are intentionally loose, so showing
> them on good moves — including a mating move — was unusable. The frozen
> fixture captures them.

---

## Milestone C — Engine integration & determinism (≈ 1 day) — ✅ done

| ID | Part | What it is | Done when |
|----|------|-----------|-----------|
| **C1** | Engine analysis: config & reuse | MultiPV=4, configurable depth; reuse each position's "after" as the next "before" (halves work); threads/hash from config | A timing check shows the reuse path halves engine calls; depth/multipv are config-driven, not hardcoded |
| **C2** | Deterministic golden-game test | `tests/fixtures/<game>.json` with **frozen evals**, header pinning engine version + depth + multipv; asserts "flags exactly these plies" | Passes **offline** against the fixture; fails loudly and instructively on an engine bump; regeneration procedure documented |
| **C3** | Live-engine smoke test (opt-in) | Marked `@pytest.mark.live`, skipped by default; checks **only** plumbing (engine starts, returns a score) — never exact classifications | `pytest` default run is 100% offline; `pytest -m live` runs the smoke test |
| **C4** | End-to-end stability + docs | Run `analyze_game` over the sample **and** the fetched real games; confirm same-ish results vs the A2 baseline; document accuracy/behaviour | `analyze_game(pgn, color) -> dict` is stable and JSON-round-trippable; README/docstring notes it won't match Chess.com exactly |

---

## Definition of Done for Phase 1

- [x] `pytest` passes **offline** (pure-logic + golden fixture).
- [x] `RUN_LIVE_TESTS=1 pytest -m live` runs the plumbing smoke test (opt-in).
- [x] `analyze_game(pgn, color) -> dict` is stable, documented, and JSON-serialisable.
- [x] Thresholds, depth, and multipv come from config — nothing hardcoded.
- [x] Every rule/tag has a deterministic unit test.
- [x] A2 baseline and C2 golden fixture both pass (no silent drift).

---

## Recommended execution order & parallelisation

```
A1 ─┬─ A2 ─┬─ B1 ─┐
    │       ├─ B2 ─┤
    │       ├─ B3 ─┼─ C1 ─ C2 ─ C4
    │       ├─ B4 ─┤        └─ C3 (independent)
    │       └─ B5 ─┤
    │       └─ B6 ─┘
```

- **Serial spine:** `A1 → A2 → (B1) → C1 → C2 → C4`. These touch shared code.
- **Parallel-friendly:** B2–B6 are independent pure functions; any can be done in
  any order once B1 (POV) lands, because they all assume normalised input.
- **Independent:** C3 (smoke test) can be written at any time.
- **Highest risk / do early:** B1 (POV is the #1 bug source) and B3 (the rule
  ladder that everything downstream reports on).

## Suggested first three steps

1. **A1** — write the schema + generate a sample output (nothing else can lock
   until the contract is explicit).
2. **A2** — freeze the Phase 0 baseline (one test, maximum safety).
3. **B1** — POV normalisation (the bug that bites hardest later).
