"""Configuration, read from environment (.env supported)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

from .winpct import SUPPRESS_WIN_PTS, Thresholds

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def stockfish_path() -> str:
    """Path to the Stockfish executable. Falls back to PATH lookup."""
    return os.getenv("STOCKFISH_PATH") or "stockfish"


def analysis_depth() -> int:
    return _int("ANALYSIS_DEPTH", 16)


def analysis_multipv() -> int:
    return _int("ANALYSIS_MULTIPV", 4)


def stockfish_threads() -> int:
    return _int("STOCKFISH_THREADS", 1)


def stockfish_hash_mb() -> int:
    return _int("STOCKFISH_HASH_MB", 256)


def analysis_thresholds() -> Thresholds:
    """Classification cut-offs from env, in win%-points of drop.

    Reads ``THRESHOLD_INACCURACY`` / ``THRESHOLD_MISTAKE`` / ``THRESHOLD_BLUNDER``
    (defaults: the 10/20/30 in :mod:`analyzer.winpct`). Raises ``ValueError``
    if the configured values are not ordered — a broken .env should fail
    loudly, not silently relabel blunders as inaccuracies.
    """
    return Thresholds(
        inaccuracy=_float("THRESHOLD_INACCURACY", Thresholds.inaccuracy),
        mistake=_float("THRESHOLD_MISTAKE", Thresholds.mistake),
        blunder=_float("THRESHOLD_BLUNDER", Thresholds.blunder),
    )


def suppression_win_pts() -> float:
    """``SUPPRESS_WIN_PTS`` env: gap to the best line that is never flagged."""
    return _float("SUPPRESS_WIN_PTS", SUPPRESS_WIN_PTS)
