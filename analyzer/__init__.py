"""WhydidIBlunder analyzer core.

The public surface is :func:`analyzer.report.analyze_game`, which returns the
stable, JSON-serialisable report dict described in ``docs/analyzer-schema.md``.

Phase 1 keeps the public surface frozen while the internals are hardened
(perspective normalisation, rule tags, phase detection, deterministic fixtures).
"""

from .engine import Engine, mate_to_cp
from .phase import PHASES, detect_phase, non_pawn_material
from .pipeline import MoveReport, analyse_game_moves, analyse_moves, read_game
from .pov import to_mover_pov
from .report import (
    SCHEMA_VERSION,
    analyze_game,
    build_report,
    summarize,
    validate_report,
)
from .terminal import terminal_eval
from .tags import fork, hanging_piece, missed_capture, pin
from .winpct import Thresholds, Verdict, accuracy_from_win_diff, classify, judge, win_pct

__all__ = [
    "Engine",
    "mate_to_cp",
    "to_mover_pov",
    "terminal_eval",
    "PHASES",
    "detect_phase",
    "non_pawn_material",
    "hanging_piece",
    "missed_capture",
    "fork",
    "pin",
    "MoveReport",
    "read_game",
    "analyse_game_moves",
    "analyse_moves",
    "SCHEMA_VERSION",
    "analyze_game",
    "build_report",
    "summarize",
    "validate_report",
    "accuracy_from_win_diff",
    "classify",
    "judge",
    "Thresholds",
    "Verdict",
    "win_pct",
]
