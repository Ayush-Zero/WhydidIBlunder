"""C4 — end-to-end stability of ``analyze_game`` (offline) + the JSON contract.

No engine: a deterministic fake analyser drives the whole pipeline, so any
nondeterminism *in our own code* (dict ordering, float drift, config leakage)
shows up as a diff between two identical runs. The frozen fixture's JSON
round-trip is pinned here too.
"""

from __future__ import annotations

import json
from pathlib import Path

import chess
import chess.engine

from analyzer.report import analyze_game, validate_report

FIXTURE = Path(__file__).parent / "fixtures" / "immortal_game.report.json"

PGN = """[Event "c4-test"]
[White "A"]
[Black "B"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *
"""


class FakeEngine:
    """Deterministic stand-in for Stockfish (same input -> same output)."""

    def __init__(self, name: str = "Fake Engine 1"):
        self.name = name

    def analyse(self, board: chess.Board, depth: int, multipv: int) -> list:
        cp = 10 * board.fullmove_number + (0 if board.turn == chess.WHITE else 1)
        score = chess.engine.PovScore(chess.engine.Cp(cp), board.turn)
        first = next(iter(board.legal_moves))
        return [{"score": score, "pv": [first]} for _ in range(multipv)]

    def close(self) -> None:
        pass


def _run(color: str = "white") -> dict:
    return analyze_game(PGN, color, engine=FakeEngine(), depth=6, multipv=3)


def test_analyze_game_is_deterministic_for_identical_engine_output():
    assert _run() == _run()


def test_report_is_valid_and_json_round_trippable():
    report = _run()
    assert validate_report(report) == []
    assert json.loads(json.dumps(report)) == report


def test_summary_is_scoped_to_the_requested_colour():
    white, black = _run("white"), _run("black")
    assert white["summary"]["user_color"] == "white"
    assert black["summary"]["user_color"] == "black"
    assert white["summary"]["moves_analysed"] == black["summary"]["moves_analysed"]


def test_invalid_colour_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="white.*black"):
        analyze_game(PGN, "green", engine=FakeEngine())


def test_frozen_fixture_validates_and_round_trips():
    raw = FIXTURE.read_text(encoding="utf-8")
    report = json.loads(raw)
    assert validate_report(report) == []
    assert json.loads(json.dumps(report)) == report
