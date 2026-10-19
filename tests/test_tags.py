"""B6 — rule-based tags (offline, no engine).

Each tag is a pure function with a crafted true case and a false case, proving
both directions. The false cases matter as much as the true ones: a tag that
fires on every move is worse than no tag at all.

Positions are built with python-chess and asserted legal before use, so a bad
FEN fails loudly at the helper rather than silently mis-testing.
"""

from __future__ import annotations

import chess
import pytest

from analyzer.tags import fork, hanging_piece, missed_capture, pin


def mv(b: chess.Board, uci: str) -> chess.Move:
    move = chess.Move.from_uci(uci)
    assert move in b.legal_moves, f"{uci} not legal in {b.fen()}"
    return move


# --------------------------------------------------------------------------- #
# hanging_piece
# --------------------------------------------------------------------------- #
# Kb1 defends Nc2 against Rc8; stepping to a1 drops the defence -> the knight
# is now attacked and undefended (one more hanging piece than before).
HANG = "2r4k/8/8/8/8/8/2N5/1K6 w - - 0 1"


def test_hanging_piece_true_when_a_move_drops_a_defender():
    b = chess.Board(HANG)
    assert hanging_piece(b, mv(b, "b1a1"))


def test_hanging_piece_false_for_a_safe_move():
    # The knight retreats to e3, which Rc8 does not attack -> nothing hangs.
    b = chess.Board(HANG)
    assert not hanging_piece(b, mv(b, "c2e3"))


def test_hanging_piece_false_on_a_quiet_opening_move():
    b = chess.Board()
    assert not hanging_piece(b, mv(b, "e2e4"))


def test_hanging_piece_does_not_refire_on_a_pre_existing_hang():
    # Nc2 is already attacked-and-undefended before the move (Ka1 does not
    # defend c2). A king move to a2 also leaves it undefended, so the count
    # stays at 1 -> the pre-existing hang is not re-flagged.
    b = chess.Board("2r4k/8/8/8/8/8/2N5/K7 w - - 0 1")
    assert not hanging_piece(b, mv(b, "a1a2"))


# --------------------------------------------------------------------------- #
# pin
# --------------------------------------------------------------------------- #
# a8-h1 diagonal: Ba8, Nd5, Ke4. King stepping to e4 lands behind its own
# knight on the diagonal -> the knight becomes absolutely pinned.
PIN_POS = "b6k/8/8/3N4/3K4/8/8/8 w - - 0 1"


def test_pin_true_when_a_move_aligns_a_piece_with_the_king():
    b = chess.Board(PIN_POS)
    assert pin(b, mv(b, "d4e4"))


def test_pin_false_when_the_king_stays_off_the_diagonal():
    b = chess.Board(PIN_POS)
    assert not pin(b, mv(b, "d4c3"))


def test_pin_false_for_a_quiet_move():
    b = chess.Board()
    assert not pin(b, mv(b, "g1f3"))


# --------------------------------------------------------------------------- #
# fork
# --------------------------------------------------------------------------- #
# Ne5 attacks d3 and f3. With the rook still on f1 only the queen (d3) is a
# valuable target; rook f1-f3 puts a second valuable piece under the knight.
FORK_POS = "7k/8/8/4n3/8/3Q4/8/K4R2 w - - 0 1"


def test_fork_true_when_a_move_walks_into_a_fork():
    b = chess.Board(FORK_POS)
    assert fork(b, mv(b, "f1f3"))


def test_fork_false_when_only_one_valuable_target_is_attacked():
    b = chess.Board(FORK_POS)
    assert not fork(b, mv(b, "f1f2"))  # f2 is not attacked by Ne5


def test_fork_false_for_a_quiet_move():
    b = chess.Board()
    assert not fork(b, mv(b, "d2d4"))


def test_fork_ignores_non_valuable_targets():
    # Ne5 attacks only the d3 pawn (value 1 < 3) -> not a fork.
    b = chess.Board("7k/8/8/4n3/8/3P4/8/K7 w - - 0 1")
    assert not fork(b, mv(b, "a1b1"))


# --------------------------------------------------------------------------- #
# missed_capture
# --------------------------------------------------------------------------- #
# Ra8 is undefended; White can take it for free (Ra1xa8) but may play a king
# move instead.
CAPT = "r6k/8/8/8/8/8/8/R6K w - - 0 1"


def test_missed_capture_true_when_a_free_capture_is_passed_up():
    b = chess.Board(CAPT)
    assert missed_capture(b, mv(b, "h1g1"))  # king move ignores the free rook


def test_missed_capture_false_when_the_free_capture_is_taken():
    b = chess.Board(CAPT)
    assert not missed_capture(b, mv(b, "a1a8"))


def test_missed_capture_false_when_the_target_is_defended():
    # Ra8 is defended by Rb8, so taking it is a trade, not free material.
    b = chess.Board("1r5k/8/8/8/8/8/8/R6K w - - 0 1")
    assert not missed_capture(b, mv(b, "h1g1"))


def test_missed_capture_false_on_a_quiet_opening_move():
    b = chess.Board()
    assert not missed_capture(b, mv(b, "e2e4"))


def test_missed_capture_handles_en_passant():
    # Real double push g2-g4 makes h4xg3 e.p. available and free (g3 undefended).
    b = chess.Board("7k/8/8/8/7p/8/6P1/K7 w - - 0 1")
    b.push_uci("g2g4")  # now Black to move with ep on g3
    ep = chess.Move.from_uci("h4g3")
    assert b.is_en_passant(ep)
    assert not missed_capture(b, ep)  # taking it -> nothing missed
    assert missed_capture(b, chess.Move.from_uci("h8h7"))  # ignoring it -> flagged


# --------------------------------------------------------------------------- #
# shared sanity: every tag is total and False on a quiet first move
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tag_fn", [hanging_piece, pin, fork, missed_capture])
def test_tags_are_total_and_false_on_a_quiet_opening_move(tag_fn):
    b = chess.Board()
    assert tag_fn(b, mv(b, "e2e4")) is False
