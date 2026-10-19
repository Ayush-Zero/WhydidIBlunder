"""Win-probability and classification math.

All quantities here are pure and deterministic — no engine, no config file
reads. This is the part the golden-game tests anchor on.

Units, stated once and enforced by the names:
  * ``win_pct`` takes **centipawns** and returns **win%-points (0-100)**.
  * ``classify``/``judge`` take a *drop* in **win%-points**.
  * ``accuracy_from_win_diff`` takes a **win%-point** difference (0-100).
  * :class:`Thresholds` cut-offs are **win%-points** of drop.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Lichess win-probability constant (centipawns -> win%).
WIN_RATE_K = 0.00368208

# Classification thresholds, in win%-points of drop (mover's perspective).
# Env overrides (THRESHOLD_* / SUPPRESS_WIN_PTS) are applied by analyzer.config;
# these module constants are the deterministic defaults tests anchor on.
INACCURACY = 10.0
MISTAKE = 20.0
BLUNDER = 30.0

# A move this close to the engine's top line is treated as a reasonable
# alternative and never flagged. Units: win%-points.
SUPPRESS_WIN_PTS = 0.2


def win_pct(cp: float) -> float:
    """Centipawns -> win probability for the side the score is measured from."""
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-WIN_RATE_K * cp)) - 1.0)


def accuracy_from_win_diff(win_diff: float) -> float:
    """Per-move accuracy from a win%-point difference (0-100), clamped 0-100.

    ``win_diff`` is in **win%-points**, NOT centipawns. A zero difference maps
    to ~100; large differences fall toward 0.
    """
    acc = 103.1668 * math.exp(-0.04354 * win_diff) - 3.1669
    return max(0.0, min(100.0, acc))


@dataclass(frozen=True)
class Thresholds:
    """Cut-offs in **win%-points** of drop. Ordered, or a bug waiting to happen."""

    inaccuracy: float = INACCURACY
    mistake: float = MISTAKE
    blunder: float = BLUNDER

    def __post_init__(self) -> None:
        if not (0 < self.inaccuracy < self.mistake < self.blunder):
            raise ValueError(
                "thresholds must satisfy 0 < inaccuracy < mistake < blunder, "
                f"got {self.inaccuracy}/{self.mistake}/{self.blunder}"
            )


@dataclass(frozen=True)
class Verdict:
    """The outcome of the precedence ladder for one move."""

    classification: str  # "ok" | "inaccuracy" | "mistake" | "blunder"
    suppressed: bool
    reason: str  # why it is "ok"; "" when not suppressed


def classify(
    drop: float,
    suppressed: bool = False,
    *,
    thresholds: Thresholds | None = None,
) -> str:
    """Map a win%-point drop to a label.

    Precedence (see plan.md): reasonable-alternative suppression wins over the
    raw threshold, so a suppressed move is always ``ok`` regardless of drop.
    ``thresholds=None`` uses the module defaults.
    """
    if suppressed:
        return "ok"
    t = Thresholds() if thresholds is None else thresholds
    if drop >= t.blunder:
        return "blunder"
    if drop >= t.mistake:
        return "mistake"
    if drop >= t.inaccuracy:
        return "inaccuracy"
    return "ok"


def judge(
    drop: float,
    *,
    forced: bool = False,
    rank: int | None = None,
    thresholds: Thresholds | None = None,
    suppress: float = SUPPRESS_WIN_PTS,
) -> Verdict:
    """The precedence ladder, stated once (plan.md "Rule precedence"):

    1. **Forced move** (exactly one legal reply) -> ``ok``, no tags.
    2. **Engine's best move** (rank 1) or **gap <= ``suppress`` win%-points**
       -> ``ok`` (a reasonable alternative).
    3. Otherwise label by the win%-drop threshold.

    Rank 2-3 is deliberately *not* a suppression reason: the second-best line
    can be far worse than the first, which would hide real blunders.
    """
    if forced:
        return Verdict("ok", True, "forced")
    if rank == 1:
        return Verdict("ok", True, "best move")
    if drop <= suppress:
        return Verdict("ok", True, "~= best")
    return Verdict(classify(drop, thresholds=thresholds), False, "")
