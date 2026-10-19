"""C3 — opt-in live-engine smoke test (plumbing only).

These launch a real Stockfish binary and are skipped unless opted in:

    $env:RUN_LIVE_TESTS='1'; .\\.venv\\Scripts\\python.exe -m pytest -m live

They assert only that the **plumbing** works — the engine starts, returns a
score, and ``analyze_game`` yields a schema-valid, JSON-serialisable report.
They deliberately assert nothing about *which* moves are flagged: exact
classifications are the golden fixture's job (`tests/test_golden_game.py`) and
they shift with the engine version.
"""

from __future__ import annotations

import json
from pathlib import Path

import chess
import pytest

from analyzer import config
from analyzer.engine import Engine
from analyzer.report import analyze_game, validate_report

pytestmark = pytest.mark.live

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "immortal_game.pgn"


def _engine() -> Engine:
    return Engine(
        config.stockfish_path(),
        threads=config.stockfish_threads(),
        hash_mb=config.stockfish_hash_mb(),
    )


def test_engine_starts_and_returns_a_score():
    with _engine() as engine:
        assert engine.name  # e.g. "Stockfish 19"
        infos = engine.analyse(chess.Board(), depth=6, multipv=1)
    assert infos, "engine returned no analysis"
    assert "score" in infos[0], infos[0]


def test_multipv_returns_that_many_lines():
    with _engine() as engine:
        infos = engine.analyse(chess.Board(), depth=6, multipv=3)
    assert len(infos) == 3


def test_analyze_game_end_to_end_produces_a_valid_report():
    report = analyze_game(
        SAMPLE.read_text(encoding="utf-8"), "white", depth=8, multipv=2
    )
    assert validate_report(report) == []
    assert report["engine"]["name"]
    assert report["engine"]["depth"] == 8
    assert report["summary"]["moves_analysed"] > 0
    # The contract is JSON-serialisable: a round-trip is lossless.
    assert json.loads(json.dumps(report)) == report
