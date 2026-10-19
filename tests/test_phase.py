"""B5 — phase detection (offline, no engine).

Crafted positions must map to the expected phase; the ply/material boundaries
are pinned so the heuristic cannot drift silently.
"""

from __future__ import annotations

import chess
import pytest

from analyzer.phase import (
    ENDGAME_MATERIAL,
    OPENING_MAX_PLY,
    PHASES,
    detect_phase,
    non_pawn_material,
)

STARTPOS = chess.Board()

# A typical middlegame position, full material (two complete armies).
MIDDLEGAME = chess.Board(
    "r1bq1rk1/pppp1ppp/2n2n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 0 10"
)

# K+R vs K — a trivially won rook endgame.
ROOK_ENDGAME = chess.Board("8/8/4k3/8/8/4K3/4R3/8 w - - 0 40")

# K+Q vs K+Q — queen endgame, still far below the material floor.
QUEEN_ENDGAME = chess.Board("8/8/4kq2/8/8/4K3/8/7Q w - - 0 30")

# Both sides stripped of queens and rooks (only K + 2N + 2B each) very early:
# an early liquidation, not an "opening" position despite the low ply.
EARLY_LIQUIDATION = chess.Board(
    "1nb1kbn1/pppppppp/8/8/8/8/PPPPPPPP/1NB1KBN1 w - - 0 12"
)


# --------------------------------------------------------------------------- #
# material counting
# --------------------------------------------------------------------------- #
def test_startpos_material_is_full():
    assert non_pawn_material(STARTPOS) == 62  # Q 9 + 2R 10 + 2B/N 12, x2 sides


def test_material_ignores_pawns_and_kings():
    # A pawn-rammed endgame is still an endgame: pawns must not count.
    pawn_heavy = chess.Board("8/8/4k3/8/8/4K3/8/PPP1PPPP w - - 0 40")
    assert non_pawn_material(pawn_heavy) == 0


def test_material_counts_both_sides_symmetrically():
    assert non_pawn_material(ROOK_ENDGAME) == 5  # one rook
    assert non_pawn_material(QUEEN_ENDGAME) == 18  # two queens


# --------------------------------------------------------------------------- #
# phase mapping
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "board,ply,expected",
    [
        (STARTPOS, 0, "opening"),
        (STARTPOS, OPENING_MAX_PLY - 1, "opening"),
        (STARTPOS, OPENING_MAX_PLY, "middlegame"),  # ply boundary
        (STARTPOS, 80, "middlegame"),  # full material can only be mid/opening
        (MIDDLEGAME, 20, "middlegame"),
        (MIDDLEGAME, 40, "middlegame"),
        (ROOK_ENDGAME, 40, "endgame"),
        (QUEEN_ENDGAME, 30, "endgame"),
        (EARLY_LIQUIDATION, 12, "endgame"),  # material beats a low ply
    ],
)
def test_crafted_positions_map_to_expected_phase(board, ply, expected):
    assert detect_phase(board, ply) == expected


def test_material_floor_boundary():
    # Build a position at exactly the floor (Q+Q+R+N = 9+9+5+3 = 26) and one
    # pawn-value above it, to pin the endgame threshold.
    def board_with(extra: chess.PieceType | None) -> chess.Board:
        b = chess.Board(None)
        b.set_piece_at(chess.E1, chess.Piece(chess.QUEEN, chess.WHITE))
        b.set_piece_at(chess.A8, chess.Piece(chess.QUEEN, chess.BLACK))
        b.set_piece_at(chess.H1, chess.Piece(chess.ROOK, chess.WHITE))
        b.set_piece_at(chess.F6, chess.Piece(chess.KNIGHT, chess.WHITE))
        b.set_piece_at(chess.E6, chess.Piece(chess.KING, chess.WHITE))
        b.set_piece_at(chess.E8, chess.Piece(chess.KING, chess.BLACK))
        if extra is not None:
            b.set_piece_at(chess.A1, chess.Piece(extra, chess.BLACK))
        return b

    at_floor = board_with(None)
    assert non_pawn_material(at_floor) == ENDGAME_MATERIAL
    assert detect_phase(at_floor, 40) == "endgame"

    just_above = board_with(chess.PAWN)  # a pawn must not push it out of endgame
    assert non_pawn_material(just_above) == ENDGAME_MATERIAL
    assert detect_phase(just_above, 40) == "endgame"

    above = board_with(chess.BISHOP)  # 26 + 3 = 29 -> middlegame at high ply
    assert non_pawn_material(above) == ENDGAME_MATERIAL + 3
    assert detect_phase(above, 40) == "middlegame"


def test_returns_one_of_the_documented_phases():
    for board in (STARTPOS, MIDDLEGAME, ROOK_ENDGAME):
        for ply in range(0, 100, 7):
            assert detect_phase(board, ply) in PHASES
