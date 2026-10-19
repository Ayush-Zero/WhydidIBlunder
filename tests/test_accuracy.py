"""B4 — accuracy metric (offline, no engine).

Anchors the documented formula ``acc = clamp(103.1668 * e^(-0.04354 * winDiff)
- 3.1669, 0, 100)`` — the coefficients are re-implemented here so a silent
change in :func:`analyzer.winpct.accuracy_from_win_diff` cannot pass — plus the
unit rule (``winDiff`` is in **win%-points**, not centipawns) and the game-mean
aggregation in :func:`analyzer.report.summarize`.
"""

from __future__ import annotations

import math

import pytest

from analyzer.pipeline import MoveReport
from analyzer.report import summarize
from analyzer.winpct import accuracy_from_win_diff, win_pct


def formula(win_diff: float) -> float:
    """The documented formula, spelled out independently of the source."""
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * win_diff) - 3.1669))


def make_move(drop: float, side: str = "white") -> MoveReport:
    return MoveReport(
        ply=1,
        move_number=1,
        side=side,
        played_san="e4",
        best_san="e4",
        eval_before_cp=25,
        eval_after_cp=25,
        mate_before=None,
        mate_after=None,
        drop=drop,
        classification="ok",
        played_rank=1,
        suppressed=True,
    )


@pytest.mark.parametrize("win_diff", [0.0, 0.2, 1.0, 5.0, 9.9, 10.0, 19.9, 20.0, 29.9, 30.0, 60.0])
def test_matches_the_documented_formula(win_diff):
    assert accuracy_from_win_diff(win_diff) == formula(win_diff)


def test_boundaries():
    assert accuracy_from_win_diff(0) == pytest.approx(100.0, abs=0.01)  # best move
    assert accuracy_from_win_diff(100) == 0.0  # raw formula goes negative -> clamped
    assert accuracy_from_win_diff(1000) == 0.0
    assert accuracy_from_win_diff(-5) == 100.0  # negative drop (score improved) -> clamped


def test_units_are_win_points_not_centipawns():
    # The trap the docs warn about: 300 centipawns fed in as "300" is total
    # loss, while a 30 win%-point drop (a blunder) still scores ~24.8.
    assert accuracy_from_win_diff(300) == 0.0
    assert accuracy_from_win_diff(30) == pytest.approx(formula(30))
    assert accuracy_from_win_diff(30) == pytest.approx(24.8, abs=0.1)
    # Sanity: a 300cp advantage is ~25 win%-points, not 300.
    assert win_pct(300) - win_pct(0) == pytest.approx(25.1, abs=0.2)


def test_monotonic():
    diffs = [0, 2, 5, 10, 20, 30, 60]
    accs = [accuracy_from_win_diff(d) for d in diffs]
    assert accs == sorted(accs, reverse=True)


def test_game_accuracy_is_the_mean_over_the_user_color_only():
    moves = [
        make_move(0.0, "white"),
        make_move(20.0, "white"),
        make_move(100.0, "black"),  # must not drag White's number down
    ]
    summary = summarize(moves, "white")
    expected = round(
        (accuracy_from_win_diff(0.0) + accuracy_from_win_diff(20.0)) / 2, 1
    )
    assert summary["accuracy"] == expected
    assert summary["moves_analysed"] == 2


def test_accuracy_is_null_when_the_color_played_no_moves():
    assert summarize([], "white")["accuracy"] is None
