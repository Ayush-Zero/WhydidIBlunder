"""Rule-based move tags — pure, offline predicates over a board + move.

These explain *why* a flagged move was bad, in the same informational spirit as
``missed_mate`` / ``allowed_mate`` (which live in the pipeline). Per the project
decision, every tag here describes an **error made by the mover**, so the move
under scrutiny is always the one that turns the position from OK to bad.

Each function takes the position *before* the move plus the move itself, and
returns ``True`` only if the move **created or worsened** the condition (the
count of offending pieces strictly increases). This attributes the error to the
move instead of re-flagging pre-existing problems on every ply.

Deliberate simplifications (documented so nobody mistakes them for SEE-level
accuracy):

* A piece counts as **defended** if any friendly piece attacks its square —
  even a pinned defender. python-chess's ``is_attacked_by`` ignores pins.
* A piece counts as **hanging** when it is attacked by the opponent and not
  defended at all. Semi-hangs (outnumbered defenders) are not detected.
* A **fork** is a single piece attacking two or more valuable targets (a king,
  or a piece worth >= 3 pawn units). Defences are ignored, so a "fork" against
  two fully defended pieces is still reported.
* ``missed_capture`` only fires for captures that win material outright (the
  landing square is undefended afterwards). Sacrificial combinations are out of
  scope.

``move`` must be legal for ``board`` (the pipeline only ever passes mainline
moves); these functions do not validate legality.
"""

from __future__ import annotations

import chess

# Piece values in pawn units, shared with phase detection.
from .phase import PIECE_VALUE

_VALUABLE_MIN = 3  # a fork target must be worth at least a minor piece


def _piece_value(piece: chess.Piece) -> int:
    return PIECE_VALUE.get(piece.piece_type, 0)  # king -> 0 (handled specially)


def _is_valuable(piece: chess.Piece) -> bool:
    return piece.piece_type == chess.KING or _piece_value(piece) >= _VALUABLE_MIN


# --------------------------------------------------------------------------- #
# counters (the "after" and "before" measurements each tag compares)
# --------------------------------------------------------------------------- #
def _hanging_count(board: chess.Board, color: chess.Color) -> int:
    """``color``'s non-king pieces that are attacked and completely undefended."""
    n = 0
    for sq, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING:
            continue
        if board.is_attacked_by(not color, sq) and not board.is_attacked_by(color, sq):
            n += 1
    return n


def _pinned_count(board: chess.Board, color: chess.Color) -> int:
    """``color``'s non-king pieces absolutely pinned to their own king."""
    n = 0
    for sq, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING:
            continue
        if board.is_pinned(color, sq):
            n += 1
    return n


def _fork_count(board: chess.Board, by_color: chess.Color, victim: chess.Color) -> int:
    """Pieces of ``by_color`` that each attack 2+ valuable ``victim`` pieces."""
    n = 0
    for sq, piece in board.piece_map().items():
        if piece.color != by_color:
            continue
        targets = 0
        for t in board.attacks(sq):
            tp = board.piece_at(t)
            if tp is not None and tp.color == victim and _is_valuable(tp):
                targets += 1
        if targets >= 2:
            n += 1
    return n


# --------------------------------------------------------------------------- #
# the tags
# --------------------------------------------------------------------------- #
def hanging_piece(board: chess.Board, move: chess.Move) -> bool:
    """The move leaves more of the mover's pieces hanging than it started with."""
    mover = board.turn
    before = _hanging_count(board, mover)
    after = board.copy()
    after.push(move)
    return _hanging_count(after, mover) > before


def pin(board: chess.Board, move: chess.Move) -> bool:
    """The move leaves more of the mover's pieces pinned than it started with."""
    mover = board.turn
    before = _pinned_count(board, mover)
    after = board.copy()
    after.push(move)
    return _pinned_count(after, mover) > before


def fork(board: chess.Board, move: chess.Move) -> bool:
    """The move lets the opponent fork more of the mover's pieces (walked into it)."""
    mover = board.turn  # the victim is the side making this move
    opponent = not mover
    before = _fork_count(board, opponent, mover)
    after = board.copy()
    after.push(move)
    return _fork_count(after, opponent, mover) > before


def _capture_is_free(board: chess.Board, capture: chess.Move) -> bool:
    """Playing ``capture`` wins material: it takes a piece that cannot be taken back."""
    if board.is_en_passant(capture):
        captured_value = PIECE_VALUE[chess.PAWN]
    else:
        captured = board.piece_at(capture.to_square)
        if captured is None or captured.color == board.turn:
            return False  # not capturing an enemy piece
        captured_value = _piece_value(captured)
    if captured_value <= 0:
        return False
    after = board.copy()
    after.push(capture)
    # can the opponent simply recapture on the landing square?
    return not after.is_attacked_by(not board.turn, capture.to_square)


def missed_capture(board: chess.Board, move: chess.Move) -> bool:
    """A capture that wins material was available and the mover did not take it."""
    winning = [
        c
        for c in board.legal_moves
        if board.is_capture(c) and _capture_is_free(board, c)
    ]
    if not winning:
        return False
    # the mover took one of the free captures -> nothing was missed
    return not any(move == c for c in winning)
