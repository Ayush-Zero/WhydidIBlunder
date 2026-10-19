"""B1 — POV normalisation (offline, no engine).

Two properties are proven here:

1. *Correctness* — :func:`analyzer.pov.to_mover_pov` returns identical results
   whichever side is anchored as "to move", and flips sign exactly once when
   the mover changes.
2. *Single entry point* — no other module in ``analyzer/`` reads a
   ``PovScore`` directly (a static source scan), so normalisation cannot
   accidentally happen twice or not at all.
"""

from __future__ import annotations

from pathlib import Path

import chess
import chess.engine
import pytest

import analyzer
from analyzer.engine import mate_to_cp
from analyzer.pov import to_mover_pov

PKG = Path(analyzer.__file__).parent


def cp_score(cp: int, turn: chess.Color) -> chess.engine.PovScore:
    return chess.engine.PovScore(chess.engine.Cp(cp), turn)


def mate_score(mate_in: int, turn: chess.Color) -> chess.engine.PovScore:
    return chess.engine.PovScore(chess.engine.Mate(mate_in), turn)


# --------------------------------------------------------------------------- #
# correctness
# --------------------------------------------------------------------------- #
def test_same_position_result_is_identical_whichever_side_is_to_move():
    # One position, evaluated +0.30 for White, anchored two ways: as White to
    # move (+30 for White) and as Black to move (-30 for Black). Same position,
    # same mover reading, no matter how the engine anchored the score.
    white_to_move = cp_score(30, chess.WHITE)
    black_to_move = cp_score(-30, chess.BLACK)

    assert to_mover_pov(white_to_move, chess.WHITE) == (30, None)   # White +0.30
    assert to_mover_pov(black_to_move, chess.WHITE) == (30, None)   # White +0.30
    assert to_mover_pov(white_to_move, chess.BLACK) == (-30, None)  # Black -0.30
    assert to_mover_pov(black_to_move, chess.BLACK) == (-30, None)  # Black -0.30


@pytest.mark.parametrize("cp", [-850, -40, 0, 40, 850])
def test_changing_the_mover_flips_the_sign_exactly_once(cp):
    score = cp_score(cp, chess.WHITE)  # anchored to White to move
    assert to_mover_pov(score, chess.WHITE) == (cp, None)
    assert to_mover_pov(score, chess.BLACK) == (-cp, None)


def test_mate_scores_keep_distance_and_sign():
    score = mate_score(3, chess.WHITE)  # White to move, mates in 3
    assert to_mover_pov(score, chess.WHITE) == (mate_to_cp(3), 3)
    assert to_mover_pov(score, chess.BLACK) == (mate_to_cp(-3), -3)


def test_m1_and_m3_stay_distinct():
    m1 = to_mover_pov(mate_score(1, chess.WHITE), chess.WHITE)
    m3 = to_mover_pov(mate_score(3, chess.WHITE), chess.WHITE)
    assert m1 != m3
    assert m1[1] == 1 and m3[1] == 3


# --------------------------------------------------------------------------- #
# single entry point (static enforcement)
# --------------------------------------------------------------------------- #
def test_no_other_module_reads_a_pov_score_directly():
    """Normalisation happens once, in analyzer/pov.py — enforced by scan."""
    offenders: list[str] = []
    for path in sorted(PKG.glob("*.py")):
        if path.name == "pov.py":
            continue
        src = path.read_text(encoding="utf-8")
        forbidden = [".pov(", ".white()", ".black()", ".relative"]
        hit = next((t for t in forbidden if t in src), None)
        if hit:
            offenders.append(f"{path.name}: {hit}")
    assert not offenders, (
        "these modules read a PovScore directly; route scores through "
        f"analyzer.pov.to_mover_pov(): {offenders}"
    )
