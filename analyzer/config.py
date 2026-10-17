"""Configuration, read from environment (.env supported)."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
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
