"""Deterministic tests for the pure classification math (no engine needed).

These are the anchors the golden-game tests (Phase 1) will build on.
"""

import pytest

from analyzer.engine import MATE_CP, mate_to_cp
from analyzer.winpct import (
    accuracy_from_win_diff,
    classify,
    win_pct,
)


def test_win_pct_even_position_is_50():
    assert win_pct(0) == 50.0


@pytest.mark.parametrize("cp", [-500, -100, -37, 0, 37, 100, 500])
def test_win_pct_is_symmetric(cp):
    assert abs((win_pct(cp) + win_pct(-cp)) - 100.0) < 1e-9


def test_win_pct_monotonic():
    assert win_pct(300) > win_pct(100) > 50.0 > win_pct(-100) > win_pct(-300)


def test_win_pct_stays_inside_for_large_evals():
    assert 0.0 < win_pct(-2000) < 1.0
    assert 99.0 < win_pct(2000) < 100.0


def test_win_pct_saturates_to_exact_extremes():
    # Extreme / mate scores underflow the sigmoid to exactly 0.0 and 100.0.
    # This is precisely why the plan clamps to 99.9 / 0.1 for display.
    assert win_pct(-20000) == 0.0
    assert win_pct(20000) == 100.0


def test_accuracy_zero_diff_is_100():
    # 103.1668 - 3.1669 == 99.9999
    assert abs(accuracy_from_win_diff(0) - 100.0) < 0.01


def test_accuracy_decreases_with_drop():
    assert accuracy_from_win_diff(5) < accuracy_from_win_diff(0)
    assert accuracy_from_win_diff(20) < accuracy_from_win_diff(5)


def test_accuracy_clamps():
    assert accuracy_from_win_diff(1000) == 0.0
    assert accuracy_from_win_diff(-5) == 100.0


@pytest.mark.parametrize(
    "drop,expected",
    [
        (0, "ok"),
        (9.9, "ok"),
        (10, "inaccuracy"),
        (19.9, "inaccuracy"),
        (20, "mistake"),
        (29.9, "mistake"),
        (30, "blunder"),
        (100, "blunder"),
    ],
)
def test_classify_thresholds(drop, expected):
    assert classify(drop) == expected


def test_classify_suppression_wins_over_threshold():
    assert classify(100, suppressed=True) == "ok"


def test_mate_encoding_preserves_distance_and_sign():
    assert mate_to_cp(1) > mate_to_cp(3) > 0
    assert mate_to_cp(-1) < mate_to_cp(-3) < 0
    assert mate_to_cp(1) < MATE_CP
    # a mating score saturates win% to ~100, not exactly 100
    assert win_pct(mate_to_cp(1)) > 99.0
    assert win_pct(mate_to_cp(-1)) < 1.0
