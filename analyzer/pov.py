"""Point-of-view normalisation — the single place engine scores enter the pipeline.

The #1 bug source in chess analysis: every eval has a point of view. Stockfish
(via python-chess) returns a :class:`chess.engine.PovScore` anchored to the
side **to move**, but the pipeline stores every eval from the **mover's** point
of view. The position *after* a played move has the *opponent* to move, so
reading the default POV there silently flips the sign of the eval.

Every score must therefore pass through :func:`to_mover_pov` exactly once, at
intake; nothing downstream touches ``PovScore`` directly.
``tests/test_pov.py`` enforces both properties (including a static check that
no other module in ``analyzer/`` reads a PovScore).

Units and POV, stated once (also enforced by the names):

* centipawns — mates encoded via :func:`analyzer.engine.mate_to_cp`.
* mate distance — signed moves-to-mate from the **mover's** POV
  (positive = the mover delivers mate), ``None`` for normal scores.
"""

from __future__ import annotations

import chess
import chess.engine

from .engine import mate_to_cp

# (centipawns, mate_distance) — always from the mover's point of view.
Eval = tuple[int, int | None]


def to_mover_pov(score: chess.engine.PovScore, mover: chess.Color) -> Eval:
    """Normalise ``score`` to ``mover``'s point of view — once, here.

    Mate scores come back as ``(mate_to_cp(mate_in), mate_in)`` so that M1 and
    M3 stay distinct in both representations, and the centipawn magnitude
    saturates :func:`analyzer.winpct.win_pct`.
    """
    sc = score.pov(mover)
    if sc.is_mate():
        mate = sc.mate()
        return mate_to_cp(mate), mate
    cp = sc.score()
    return (0 if cp is None else cp), None
