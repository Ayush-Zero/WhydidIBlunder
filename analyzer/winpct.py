"""Win-probability and classification math.

All quantities here are pure and deterministic — no engine involved. This is
the part the golden-game tests anchor on.

Units, stated once and enforced by the names:
  * ``win_pct`` takes **centipawns** and returns **win%-points (0-100)**.
  * ``classify`` takes a *drop* in **win%-points**.
  * ``accuracy_from_win_diff`` takes a **win%-point** difference (0-100).
"""

from __future__ import annotations

import math

# Lichess win-probability constant (centipawns -> win%).
WIN_RATE_K = 0.00368208

# Classification thresholds, in win%-points of drop (mover's perspective).
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


def classify(drop: float, suppressed: bool = False) -> str:
    """Map a win%-point drop to a label.

    Precedence (see plan.md): reasonable-alternative suppression wins over the
    raw threshold, so a suppressed move is always ``ok`` regardless of drop.
    """
    if suppressed:
        return "ok"
    if drop >= BLUNDER:
        return "blunder"
    if drop >= MISTAKE:
        return "mistake"
    if drop >= INACCURACY:
        return "inaccuracy"
    return "ok"
