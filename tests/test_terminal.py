"""B2 — terminal & mate encoding (offline, no engine).

Covers the Phase 0 bug class: a delivered checkmate must read as +M1 for the
mover, *not* as the mover being mated. Every game-over flavour is checked so
the pipeline can guarantee "never send a game-over position to the engine".
"""

from __future__ import annotations

import chess
import pytest

from analyzer.engine import mate_to_cp
from analyzer.terminal import terminal_eval
from analyzer.winpct import win_pct

# 1. f3 e5 2. g4 Qh4# — Black delivers mate; White is mated.
FOOLS_MATE = "rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3"
# Ka8 vs Kc7 + Qc5: Black to move, no check, no legal move -> stalemate.
STALEMATE = "k7/2K5/8/2Q5/8/8/8/8 b - - 0 1"
KING_ONLY = "4k3/8/8/8/8/8/8/4K3 w - - 0 1"  # insufficient material
STARTPOS = chess.Board()


def test_non_terminal_positions_return_none():
    assert terminal_eval(STARTPOS, chess.WHITE) is None
    assert terminal_eval(STARTPOS, chess.BLACK) is None
    mid = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3")
    assert terminal_eval(mid, chess.BLACK) is None
    assert terminal_eval(mid, chess.WHITE) is None


def test_delivered_checkmate_is_max_for_the_mover():
    board = chess.Board(FOOLS_MATE)
    assert board.is_checkmate()
    assert terminal_eval(board, chess.BLACK) == (mate_to_cp(1), 1)


def test_being_mated_is_min_for_the_mover():
    board = chess.Board(FOOLS_MATE)
    assert terminal_eval(board, chess.WHITE) == (mate_to_cp(-1), -1)


def test_stalemate_is_a_draw_for_both_sides():
    board = chess.Board(STALEMATE)
    assert board.is_stalemate()
    assert terminal_eval(board, chess.BLACK) == (0, None)
    assert terminal_eval(board, chess.WHITE) == (0, None)


def test_insufficient_material_is_a_draw_for_both_sides():
    board = chess.Board(KING_ONLY)
    assert board.is_game_over() and not board.is_checkmate()
    assert terminal_eval(board, chess.WHITE) == (0, None)
    assert terminal_eval(board, chess.BLACK) == (0, None)


def test_seventy_five_move_rule_is_a_draw():
    board = chess.Board()
    board.halfmove_clock = 150  # no capture/pawn move for 75 full moves
    assert board.is_game_over()
    assert terminal_eval(board, chess.WHITE) == (0, None)


def test_fivefold_repetition_is_a_draw():
    board = chess.Board()
    for _ in range(4):  # 4 x (Nf3 Nf6 Ng8 Ng1) -> startpos seen 5 times
        for mv in ("g1f3", "g8f6", "f3g1", "f6g8"):
            board.push_uci(mv)
    assert board.is_fivefold_repetition()
    assert terminal_eval(board, chess.WHITE) == (0, None)


def test_mate_distance_survives_the_encoding():
    # M1 and M3 stay distinct in both representations, so the UI can show
    # "mate in 3" vs "mate in 1".
    assert mate_to_cp(1) != mate_to_cp(3)
    assert mate_to_cp(1) > mate_to_cp(3) > 0


def test_win_pct_collapses_mate_distances():
    # Documented quirk: the sigmoid flattens every mate score to ~100.0 (~0.0
    # for the opponent), so the win%-drop maths cannot tell M1 from M3 — the
    # gap between them is ~1e-13 win%-points, far below the 0.2 suppression.
    # This is precisely why missed_mate/allowed_mate are informational tags
    # carrying the mate distance, rather than part of the thresholds (plan.md).
    for mate_in in (1, 3, 8):
        assert win_pct(mate_to_cp(mate_in)) > 99.999
        assert win_pct(mate_to_cp(-mate_in)) < 0.001
    assert win_pct(mate_to_cp(1)) - win_pct(mate_to_cp(3)) < 0.2


@pytest.mark.parametrize(
    "fen,mover,expected",
    [
        (FOOLS_MATE, chess.BLACK, (mate_to_cp(1), 1)),
        (FOOLS_MATE, chess.WHITE, (mate_to_cp(-1), -1)),
        (STALEMATE, chess.BLACK, (0, None)),
        (STALEMATE, chess.WHITE, (0, None)),
    ],
)
def test_result_shape_is_always_centipawns_plus_mate(fen, mover, expected):
    result = terminal_eval(chess.Board(fen), mover)
    assert result == expected
    cp, mate = result
    assert isinstance(cp, int)
    assert mate is None or (isinstance(mate, int) and abs(mate) == 1)
