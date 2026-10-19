"""Terminal-position evaluation — game-over positions never reach the engine.

Why this exists (Phase 0 bug): Stockfish reports ``mate 0`` on a mated position,
which naively reads as *the mover being mated* and mislabels a delivered
checkmate as a ~100-point blunder. So the pipeline never asks the engine about
a game-over position — :func:`terminal_eval` produces the eval directly:

* **delivered checkmate** — the best possible outcome for the mover (+M1);
* **any other game-over** (stalemate, insufficient material, repetition,
  50/75-move) — a draw (0).

Units and POV match :func:`analyzer.pov.to_mover_pov`: centipawns from the
mover's POV, signed mate distance in moves.
"""

from __future__ import annotations

import chess

from .engine import mate_to_cp
from .pov import Eval


def terminal_eval(board: chess.Board, mover: chess.Color) -> Eval | None:
    """Eval for a game-over ``board`` from ``mover``'s POV, else ``None``.

    Returning ``None`` for non-terminal positions is the contract that lets the
    pipeline decide "this position must be searched" in one comparison.
    """
    if not board.is_game_over():
        return None
    if board.is_checkmate():
        # The side to move is the mated one; the mover delivered mate iff the
        # mover is the *other* side.
        mate = 1 if mover != board.turn else -1
        return mate_to_cp(mate), mate
    return 0, None
