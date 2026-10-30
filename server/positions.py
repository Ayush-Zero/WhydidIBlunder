"""Read-only position bridge for the Phase 3 review board.

The frozen report deliberately carries SAN and evaluations, not FENs.  The
board needs a position per ply, and the phase summary needs the same
opening/middlegame/endgame heuristic the analyzer already owns.  This module
replays the report's SANs from the standard starting position and returns both,
without touching the engine or the stored report shape.

Why replay instead of a schema bump:

* C2 refuses games that start from a custom position, so every stored report
  starts from the standard position and its SAN sequence is sufficient.
* Keeping the report at schema v1 means no stored-report migration and no
  reshaping of the body served verbatim by C5.
* ``detect_phase`` stays the single implementation of the phase heuristic.

The endpoint in :mod:`server.api` is a pure read of ``game_reports``.  The
reconstruction helper is deliberately engine-free so it can also be reused by
the Phase 3 try-again grader without importing a second chess rules engine.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

from analyzer.phase import detect_phase

__all__ = ["Replay", "ReplayPosition", "ReportReplayError", "replay_report"]


class ReportReplayError(ValueError):
    """A stored report cannot be replayed by the standard rules of chess.

    The API maps this to ``409 Conflict``: the report exists but is not
    renderable, rather than pretending the game is missing.
    """


@dataclass(frozen=True)
class ReplayPosition:
    """One ply paired with the positions immediately before and after it."""

    ply: int
    fen_before: str
    fen_after: str
    #: Phase of ``fen_before``: the position in which the mover chose this move.
    phase: str


@dataclass(frozen=True)
class Replay:
    """The starting position plus one entry per report move."""

    starting_fen: str
    positions: list[ReplayPosition]


def _ply(move: object, expected: int) -> int:
    if not isinstance(move, dict):
        raise ReportReplayError(f"move {expected} is not an object")

    ply = move.get("ply")
    if isinstance(ply, bool) or not isinstance(ply, int) or ply != expected:
        raise ReportReplayError(
            f"move {expected} has ply {ply!r}; expected {expected}"
        )
    return ply


def _side(move: dict, expected: str) -> None:
    side = move.get("side")
    if side != expected:
        raise ReportReplayError(
            f"move {move['ply']} has side {side!r}; expected {expected}"
        )


def replay_report(report: object) -> Replay:
    """Replay a stored report's SANs from the standard starting position.

    ``report`` is the JSON object stored in ``game_reports.report_json``.  Only
    ``moves[].ply``, ``moves[].side`` and ``moves[].san`` are read.  The report's
    evaluations and the user colour do not affect the positions.

    Raises :class:`ReportReplayError` for a malformed or illegal sequence.  The
    caller should not serve a half-replayed board; this function returns either
    the complete sequence or nothing.
    """
    if not isinstance(report, dict):
        raise ReportReplayError("report is not an object")

    moves = report.get("moves")
    if not isinstance(moves, list):
        raise ReportReplayError("report.moves is not a list")

    board = chess.Board()
    starting_fen = board.fen()
    positions: list[ReplayPosition] = []

    for expected, move in enumerate(moves, start=1):
        ply = _ply(move, expected)
        side = "white" if board.turn == chess.WHITE else "black"
        _side(move, side)

        san = move.get("san")
        if not isinstance(san, str) or not san:
            raise ReportReplayError(f"move {ply} has no SAN")

        fen_before = board.fen()
        # ``fen_before`` has completed ``ply - 1`` half-moves.  Passing that
        # count is what makes ``phase`` describe the position the mover faced,
        # not the position after their move.
        phase = detect_phase(board, ply - 1)

        try:
            board.push_san(san)
        except ValueError as exc:
            raise ReportReplayError(
                f"move {ply} ({san!r}) is not legal in the stored position"
            ) from exc

        positions.append(
            ReplayPosition(
                ply=ply,
                fen_before=fen_before,
                fen_after=board.fen(),
                phase=phase,
            )
        )

    return Replay(starting_fen=starting_fen, positions=positions)
