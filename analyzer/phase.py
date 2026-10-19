"""Opening / middlegame / endgame detection from ply + material heuristics.

Pure and engine-free: the phase is a *hint* for the UI and the explanation
layer ("this blunder happened in the endgame"), never part of classification.

The heuristic, in order (each rule subsumes the ones below it):

1. **Opening** — first ``OPENING_MAX_PLY`` half-moves, unless the material
   collapse says endgame (a 5-move miniature is not an "opening" position).
2. **Endgame** — non-pawn material at or below ``ENDGAME_MATERIAL`` pawn
   units. The side-to-move's material is not special-cased: the phase is a
   property of the position, not of the player being analysed.
3. **Middlegame** — everything else.

Material is counted in conventional pawn units: pawn 1, knight/bishop 3,
rook 5, queen 9, king 0 (kings never leave the board, so they don't move the
needle). Pawns are excluded from the endgame test because a rook endgame with
8 pawns is still an endgame.
"""

from __future__ import annotations

import chess

PHASES = ("opening", "middlegame", "endgame")

# Half-moves considered "opening" regardless of material (10 full moves).
OPENING_MAX_PLY = 20

# Non-pawn material (pawn units, both sides) at/below which it's an endgame.
# Calibration points: full material = 62 (per side: Q 9 + 2R 10 + 2B/N 12 = 31);
# K+R vs K = 5; K+Q vs K+Q = 18; rook + minor vs three minors = 16.
ENDGAME_MATERIAL = 26

PIECE_VALUE = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


def non_pawn_material(board: chess.Board) -> int:
    """Total non-pawn material on ``board`` in pawn units (both sides)."""
    return sum(
        PIECE_VALUE[piece_type] * len(board.pieces(piece_type, color))
        for piece_type in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN)
        for color in (chess.WHITE, chess.BLACK)
    )


def detect_phase(board: chess.Board, ply: int) -> str:
    """Classify ``board`` (after ``ply`` half-moves) as opening/middlegame/endgame."""
    if ply < OPENING_MAX_PLY and non_pawn_material(board) > ENDGAME_MATERIAL:
        return "opening"
    if non_pawn_material(board) <= ENDGAME_MATERIAL:
        return "endgame"
    return "middlegame"
