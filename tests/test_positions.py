"""Phase 3 A1 — replay stored report SANs into positions and phases.

Pure, offline tests for :mod:`server.positions`.  The endpoint tests in
``tests/test_api_positions.py`` cover the HTTP surface; this file pins the
reconstruction itself:

* every ply of the golden report gets the right FEN before and after;
* ``phase`` is computed for the position the mover faced;
* an opening/middlegame/endgame sequence is replayed end to end;
* corrupt or out-of-order reports fail closed.
"""

from __future__ import annotations

import json
from pathlib import Path

import chess
import pytest

from analyzer.phase import detect_phase
from server.positions import ReportReplayError, replay_report

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "immortal_game.report.json").read_text(
        encoding="utf-8"
    )
)

# A fixed, legal line that trades enough material to cross into an endgame.
# It is a test fixture, not a game we claim is interesting: its only job is to
# exercise all three phase labels without an engine.
TRADES_TO_ENDGAME = [
    "b3",
    "h6",
    "c3",
    "Nf6",
    "Qc2",
    "g5",
    "Qe4",
    "Nc6",
    "Nh3",
    "Nxe4",
    "c4",
    "Nxd2",
    "Bxd2",
    "Bg7",
    "Kd1",
    "f6",
    "Nxg5",
    "fxg5",
    "Bxg5",
    "hxg5",
    "e3",
    "Bxa1",
    "h4",
    "gxh4",
    "a4",
    "Rb8",
    "Na3",
    "d6",
    "Kd2",
    "Rf8",
    "Rh3",
    "Ne5",
    "Rh1",
    "Nxc4+",
    "bxc4",
    "Rxf2+",
    "Be2",
    "b5",
    "Rxa1",
    "Qd7",
    "Nb1",
    "bxc4",
    "Ra2",
    "Rxe2+",
    "Kxe2",
    "Qg4+",
    "Kd2",
    "Qxg2+",
    "Ke1",
    "Rb4",
    "Rxg2",
]


def _report_from_sans(sans: list[str]) -> dict:
    """Build the minimum report shape ``replay_report`` needs."""
    board = chess.Board()
    moves = []
    for ply, san in enumerate(sans, start=1):
        moves.append(
            {
                "ply": ply,
                "side": "white" if board.turn == chess.WHITE else "black",
                "san": san,
            }
        )
        board.push_san(san)
    return {"moves": moves}


def test_replays_every_ply_of_the_golden_report():
    replay = replay_report(FIXTURE)

    assert replay.starting_fen == chess.STARTING_FEN
    assert len(replay.positions) == len(FIXTURE["moves"])

    board = chess.Board()
    for index, (move, position) in enumerate(
        zip(FIXTURE["moves"], replay.positions), start=1
    ):
        assert position.ply == index
        assert position.fen_before == board.fen()
        assert position.phase == detect_phase(board, index - 1)

        board.push_san(move["san"])
        assert position.fen_after == board.fen()


def test_phase_describes_the_position_before_the_move():
    positions = replay_report(FIXTURE).positions

    # The immortal game's material is still full, so ply 20 is the last
    # opening position and ply 21 is the first middlegame position.
    assert positions[19].ply == 20
    assert positions[19].phase == "opening"
    assert positions[20].ply == 21
    assert positions[20].phase == "middlegame"


def test_replay_exercises_all_three_phases():
    positions = replay_report(_report_from_sans(TRADES_TO_ENDGAME)).positions

    phases = {position.phase for position in positions}
    assert phases == {"opening", "middlegame", "endgame"}


def test_empty_moves_is_a_valid_empty_replay():
    replay = replay_report({"moves": []})

    assert replay.starting_fen == chess.STARTING_FEN
    assert replay.positions == []


def test_an_illegal_san_is_rejected():
    report = {"moves": [{"ply": 1, "side": "white", "san": "NotAMove"}]}

    with pytest.raises(ReportReplayError, match="not legal"):
        replay_report(report)


def test_the_wrong_side_is_rejected():
    report = {"moves": [{"ply": 1, "side": "black", "san": "e4"}]}

    with pytest.raises(ReportReplayError, match="side"):
        replay_report(report)


def test_a_gap_in_ply_numbers_is_rejected():
    report = _report_from_sans(["e4", "e5", "Nf3"])
    report["moves"][1]["ply"] = 3

    with pytest.raises(ReportReplayError, match="expected 2"):
        replay_report(report)


def test_a_missing_moves_list_is_rejected():
    with pytest.raises(ReportReplayError, match="moves"):
        replay_report({"schema_version": 1})
