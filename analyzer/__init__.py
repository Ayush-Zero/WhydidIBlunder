"""WhydidIBlunder analyzer core.

Phase 0: a minimal, dependency-light pipeline that turns a PGN into a per-move
report. Phase 1 will grow this (rule tags, phase detection, deterministic
fixtures) without changing the public surface: ``analyze_game``.
"""

from .engine import Engine, mate_to_cp
from .pipeline import MoveReport, analyze_game
from .winpct import accuracy_from_win_diff, classify, win_pct

__all__ = [
    "Engine",
    "mate_to_cp",
    "MoveReport",
    "analyze_game",
    "accuracy_from_win_diff",
    "classify",
    "win_pct",
]
