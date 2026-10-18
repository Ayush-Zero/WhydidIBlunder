"""Turn a PGN into per-move reports.

Perspective rule (the #1 bug source): every eval is normalized to the **mover's**
point of view at the single point it enters this module. The position *after* a
move has the opponent to move, so we convert with ``score.pov(mover)`` rather
than trusting the default side-to-move point of view.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import chess
import chess.pgn

from .engine import Engine, mate_to_cp
from .winpct import SUPPRESS_WIN_PTS, classify, win_pct


@dataclass
class MoveReport:
    """One analysed ply. Units and point-of-view are documented in
    ``docs/analyzer-schema.md``."""

    ply: int
    move_number: int
    side: str  # "white" / "black"
    played_san: str
    best_san: str
    eval_before_cp: int  # centipawns, mover's POV (mates via MATE_CP)
    eval_after_cp: int  # centipawns, mover's POV
    mate_before: int | None  # signed mate distance in moves, mover's POV
    mate_after: int | None
    drop: float  # win%-points; positive = worse for the mover
    classification: str
    played_rank: int | None
    suppressed: bool
    suppress_reason: str = ""
    tags: list[str] = field(default_factory=list)
    pv_san: list[str] = field(default_factory=list)


def read_game(pgn_text: str) -> chess.pgn.Game:
    """Parse the first game in ``pgn_text``; raise ``ValueError`` if there is none."""
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        raise ValueError("No game found in the PGN text")
    return game


def _score(info: dict, pov: chess.Color) -> tuple[int, int | None]:
    """``(centipawns, mate_in)`` for ``info`` as seen by ``pov``.

    Mates are encoded via :data:`MATE_CP` so they saturate :func:`win_pct`, and
    the signed mate distance is returned separately so M1 and M3 stay distinct.
    """
    sc = info["score"].pov(pov)
    if sc.is_mate():
        return mate_to_cp(sc.mate()), sc.mate()
    cp = sc.score()
    return (0 if cp is None else cp), None


def _side_name(color: chess.Color) -> str:
    return "white" if color == chess.WHITE else "black"


def _is_mating(info: dict, pov: chess.Color) -> bool:
    sc = info["score"].pov(pov)
    return sc.is_mate() and sc.mate() > 0


def _is_mated(info: dict, pov: chess.Color) -> bool:
    sc = info["score"].pov(pov)
    return sc.is_mate() and sc.mate() < 0


def analyse_game_moves(
    game: chess.pgn.Game,
    *,
    engine: Engine,
    depth: int = 16,
    multipv: int = 4,
) -> list[MoveReport]:
    """Analyse every move of ``game``.

    Each position is searched once and reused as the next move's "before"
    (halves the work). Classification is from the mover's perspective.
    """
    moves = list(game.mainline_moves())

    board = game.board()
    boards = [board.copy()]
    for mv in moves:
        board.push(mv)
        boards.append(board.copy())

    # positions 0..n, each analysed exactly once; terminal positions are skipped
    analyses = [
        None if b.is_game_over() else engine.analyse(b, depth=depth, multipv=multipv)
        for b in boards
    ]

    reports: list[MoveReport] = []
    for i, mv in enumerate(moves):
        pos = boards[i]
        mover = pos.turn

        before_lines = analyses[i]
        after_board = boards[i + 1]

        before_cp, mate_before = _score(before_lines[0], mover)
        # "After" has the opponent to move -> convert to the mover's POV.
        # Terminal positions are not sent to the engine: a delivered checkmate is
        # the best possible outcome for the mover (max), any other game-over is a
        # draw. Without this, Stockfish's "mate 0" on a mated position is read as
        # the mover being mated, mislabelling the checkmating move as a blunder.
        if after_board.is_checkmate():
            after_cp, mate_after = mate_to_cp(1), 1
        elif after_board.is_game_over():
            after_cp, mate_after = 0, None
        else:
            after_cp, mate_after = _score(analyses[i + 1][0], mover)

        drop = win_pct(before_cp) - win_pct(after_cp)

        # Where did the played move rank among the MultiPV lines?
        rank: int | None = None
        for idx, line in enumerate(before_lines):
            pv = line.get("pv") or []
            if pv and pv[0] == mv:
                rank = idx + 1
                break

        # Precedence #2: suppression. A forced move and the engine's own best
        # move can never be a mistake. Everything else is judged purely on the
        # win% gap to the best line — rank 2-3 is NOT a reason to suppress, since
        # the second-best line can be far worse than the first (see plan.md).
        forced = pos.legal_moves.count() == 1
        suppressed = False
        reason = ""
        if forced:
            suppressed, reason = True, "forced"
        elif rank == 1:
            suppressed, reason = True, "best move"
        elif drop <= SUPPRESS_WIN_PTS:
            suppressed, reason = True, "~= best"

        # Precedence #1: informational mate tags. Recorded as tags, but they do
        # NOT suppress a win%-threshold classification — walking into a forced
        # mate from a playable position is a real blunder. Forced moves get no
        # tag: there was no choice to make.
        after_info = analyses[i + 1][0] if analyses[i + 1] else None
        after_is_mating = after_board.is_checkmate() or (
            after_info is not None and _is_mating(after_info, mover)
        )
        tags: list[str] = []
        if not forced:
            if _is_mating(before_lines[0], mover) and not after_is_mating:
                tags.append("missed_mate")
            if after_info is not None and _is_mated(after_info, mover):
                tags.append("allowed_mate")

        played_san = pos.copy().san(mv)
        best_move = before_lines[0]["pv"][0]
        best_san = pos.copy().san(best_move)

        pv_san: list[str] = []
        pv_board = pos.copy()
        for pv_move in (before_lines[0].get("pv") or [])[:6]:
            pv_san.append(pv_board.san(pv_move))
            pv_board.push(pv_move)

        reports.append(
            MoveReport(
                ply=i + 1,
                move_number=i // 2 + 1,
                side=_side_name(mover),
                played_san=played_san,
                best_san=best_san,
                eval_before_cp=before_cp,
                eval_after_cp=after_cp,
                mate_before=mate_before,
                mate_after=mate_after,
                drop=drop,
                classification=classify(drop, suppressed),
                played_rank=rank,
                suppressed=suppressed,
                suppress_reason=reason,
                tags=tags,
                pv_san=pv_san,
            )
        )

    return reports


def analyse_moves(
    pgn_text: str,
    *,
    engine: Engine,
    depth: int = 16,
    multipv: int = 4,
) -> list[MoveReport]:
    """Convenience: parse the first game in ``pgn_text`` and analyse its moves."""
    return analyse_game_moves(
        read_game(pgn_text), engine=engine, depth=depth, multipv=multipv
    )
