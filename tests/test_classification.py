"""B3 — classification ladder: suppression + precedence + thresholds (offline).

Table-driven coverage of *every* branch of :func:`analyzer.winpct.judge`, the
threshold boundaries (9.9 / 10 / 19.9 / 20 / 29.9 / 30), and the config
overrides (``THRESHOLD_*`` / ``SUPPRESS_WIN_PTS`` env).
"""

from __future__ import annotations

import pytest

from analyzer import config
from analyzer.winpct import (
    INACCURACY,
    MISTAKE,
    BLUNDER,
    SUPPRESS_WIN_PTS,
    Thresholds,
    classify,
    judge,
)

# --------------------------------------------------------------------------- #
# the ladder, branch by branch
# --------------------------------------------------------------------------- #
def test_forced_move_beats_everything():
    # Precedence 1: no choice was made, so even a catastrophic drop is "ok".
    v = judge(99.0, forced=True, rank=None)
    assert (v.classification, v.suppressed, v.reason) == ("ok", True, "forced")


def test_forced_beats_rank_1():
    v = judge(0.0, forced=True, rank=1)
    assert v.reason == "forced"


def test_engine_best_move_is_suppressed():
    # Precedence 2a: rank 1 is a reasonable alternative by definition.
    v = judge(0.5, forced=False, rank=1)
    assert (v.classification, v.suppressed, v.reason) == ("ok", True, "best move")


def test_gap_within_suppress_is_suppressed():
    # Precedence 2b: essentially equal to the best line.
    v = judge(SUPPRESS_WIN_PTS, forced=False, rank=4)
    assert (v.classification, v.suppressed, v.reason) == ("ok", True, "~= best")


def test_gap_just_above_suppress_falls_through_to_thresholds():
    v = judge(SUPPRESS_WIN_PTS + 1e-9, forced=False, rank=4)
    assert (v.classification, v.suppressed, v.reason) == ("ok", False, "")


def test_rank_2_or_3_is_not_a_suppression_reason():
    # The regression Phase 0 caught: rank-2 lines can be far worse than rank 1.
    for rank in (2, 3, 4):
        v = judge(BLUNDER, forced=False, rank=rank)
        assert (v.classification, v.suppressed) == ("blunder", False), rank


@pytest.mark.parametrize(
    "drop,expected",
    [
        (9.9, "ok"),
        (10.0, "inaccuracy"),
        (19.9, "inaccuracy"),
        (20.0, "mistake"),
        (29.9, "mistake"),
        (30.0, "blunder"),
        (100.0, "blunder"),
    ],
)
def test_threshold_boundaries(drop, expected):
    v = judge(drop, forced=False, rank=4)
    assert v.classification == expected
    assert v.suppressed is False


def test_suppression_always_wins_over_a_threshold_label():
    assert classify(100.0, suppressed=True) == "ok"


# --------------------------------------------------------------------------- #
# configurable thresholds
# --------------------------------------------------------------------------- #
def test_custom_thresholds_change_the_labels():
    t = Thresholds(inaccuracy=5.0, mistake=15.0, blunder=25.0)
    assert judge(16.0, rank=4, thresholds=t).classification == "mistake"
    assert judge(16.0, rank=4).classification == "inaccuracy"  # default 10/20/30


def test_thresholds_must_be_ordered():
    with pytest.raises(ValueError, match="inaccuracy < mistake < blunder"):
        Thresholds(inaccuracy=20.0, mistake=10.0, blunder=30.0)


def test_thresholds_must_be_positive():
    with pytest.raises(ValueError):
        Thresholds(inaccuracy=0.0, mistake=10.0, blunder=30.0)


def test_config_defaults_match_the_module_constants(monkeypatch):
    monkeypatch.delenv("THRESHOLD_INACCURACY", raising=False)
    monkeypatch.delenv("THRESHOLD_MISTAKE", raising=False)
    monkeypatch.delenv("THRESHOLD_BLUNDER", raising=False)
    t = config.analysis_thresholds()
    assert (t.inaccuracy, t.mistake, t.blunder) == (INACCURACY, MISTAKE, BLUNDER)


def test_env_overrides_the_thresholds(monkeypatch):
    monkeypatch.setenv("THRESHOLD_BLUNDER", "50")
    assert config.analysis_thresholds().blunder == 50.0


def test_unparseable_env_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("THRESHOLD_MISTAKE", "twenty")
    assert config.analysis_thresholds().mistake == MISTAKE


def test_unordered_env_thresholds_fail_loudly(monkeypatch):
    # A broken .env must not silently relabel blunders as inaccuracies.
    monkeypatch.setenv("THRESHOLD_BLUNDER", "5")
    with pytest.raises(ValueError):
        config.analysis_thresholds()


def test_suppress_env_override(monkeypatch):
    monkeypatch.delenv("SUPPRESS_WIN_PTS", raising=False)
    assert config.suppression_win_pts() == SUPPRESS_WIN_PTS
    monkeypatch.setenv("SUPPRESS_WIN_PTS", "1.5")
    assert config.suppression_win_pts() == 1.5
