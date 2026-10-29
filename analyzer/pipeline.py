"""Turn a PGN into per-move reports.

Perspective rule (the #1 bug source): every eval is normalized to the **mover's**
point of view at the single point it enters the pipeline —
:func:`analyzer.pov.to_mover_pov`. This module never reads a raw engine score
otherwise; ``tests/test_pov.py`` enforces that.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import chess
import chess.pgn

from .cache import PositionCache
from .config import (
    analysis_depth,
    analysis_multipv,
    analysis_thresholds,
    suppression_win_pts,
)
from .engine import Engine
from .pov import to_mover_pov
from .tags import fork, hanging_piece, missed_capture, pin
from .terminal import terminal_eval
from .winpct import judge, win_pct


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


def _side_name(color: chess.Color) -> str:
    return "white" if color == chess.WHITE else "black"


def _analyse_cached(
    engine: Engine,
    board: chess.Board,
    depth: int,
    multipv: int,
    cache: PositionCache | None,
    timeout: float | None = None,
) -> list:
    """``engine.analyse`` with the B1 cache in front of it.

    A cache *hit at a deeper depth than requested* is returned as-is rather than
    truncated, because more search is strictly better — the caller only reads the
    lines, and the depth actually used is recorded in the report's engine header
    by :mod:`analyzer.report`.
    """
    if cache is None:
        try:
            return engine.analyse(board, depth=depth, multipv=multipv, timeout=timeout)
        except TypeError:
            return engine.analyse(board, depth=depth, multipv=multipv)

    version = PositionCache.engine_version(engine)
    hit = cache.lookup(
        fen=board.fen(),
        engine_version=version,
        depth=depth,
        multipv=multipv,
        board=board,
    )
    if hit is not None:
        return hit[1]

    try:
        lines = engine.analyse(board, depth=depth, multipv=multipv, timeout=timeout)
    except TypeError:
        lines = engine.analyse(board, depth=depth, multipv=multipv)
    cache.store(
        fen=board.fen(),
        engine_version=version,
        depth=depth,
        multipv=multipv,
        lines=lines,
        board=board,
    )
    return lines


def analyse_game_moves(
    game: chess.pgn.Game,
    *,
    engine: Engine,
    depth: int | None = None,
    multipv: int | None = None,
    cache: PositionCache | None = None,
    per_move_timeout: float | None = None,
) -> list[MoveReport]:
    """Analyse every move of ``game``.

    A thin wrapper over :func:`iter_game_moves` that collects the whole game.
    Kept because it is the natural thing for a caller that wants a result, not
    a stream; the worker (B3) drives the iterator instead so it can persist each
    ply and honour a cancel between them.

    ``depth``/``multipv`` default to the configured values
    (``ANALYSIS_DEPTH`` / ``ANALYSIS_MULTIPV``) — they are never hardcoded here.

    ``cache`` (B1) is consulted before every engine call and filled after it, so
    a repeated position — across games, not just within one — is searched once.
    """
    return [
        report
        for _, _, report in iter_game_moves(
            game, engine=engine, depth=depth, multipv=multipv, cache=cache, per_move_timeout=per_move_timeout
        )
    ]


def iter_game_moves(
    game: chess.pgn.Game,
    *,
    engine: Engine,
    depth: int | None = None,
    multipv: int | None = None,
    cache: PositionCache | None = None,
    per_move_timeout: float | None = None,
):
    """Yield ``(index, total_plies, MoveReport)`` one ply at a time.

    This is the shape the worker needs. Analysing a whole game in one call means
    a cancel cannot be honoured until it is over and progress cannot be reported
    at all; a generator lets the caller act *between* plies — persist a row,
    record progress, check for cancellation.

    The work is identical to the collect-everything version, and the two share
    this code, so "the worker's numbers match the CLI's" is structural rather
    than a coincidence maintained by hand.

    Positions are still searched **exactly once** each: ``before_lines`` for ply
    ``i`` is the same analysis that will be ply ``i+1``'s "before", carried in
    ``previous_analysis`` rather than recomputed. The only subtlety introduced by
    laziness is that position *i+1* is searched when ply *i+1* is requested, so
    an ``n``-ply game still costs ``n + 1`` searches.
    """
    depth = analysis_depth() if depth is None else depth
    multipv = analysis_multipv() if multipv is None else multipv

    moves = list(game.mainline_moves())

    # Classification cut-offs, from config (env-overridable) — read once.
    thresholds = analysis_thresholds()
    suppress = suppression_win_pts()

    board = game.board()
    boards = [board.copy()]
    for mv in moves:
        board.push(mv)
        boards.append(board.copy())

    total = len(moves)

    def analysed(index: int):
        """Position ``index``'s lines, or ``None`` for a terminal position.

        Terminal positions are never sent to the engine — a delivered checkmate
        is the best possible outcome for the mover (see ``analyzer/terminal.py``).
        """
        position = boards[index]
        if position.is_game_over():
            return None
        return _analyse_cached(engine, position, depth, multipv, cache, timeout=per_move_timeout)

    before_lines = analysed(0)
    for i, mv in enumerate(moves):
        pos = boards[i]
        mover = pos.turn

        after_board = boards[i + 1]

        # Position i+1 is searched here, and it is the *same* analysis the next
        # iteration will use as its "before" (see the carry at the end of the
        # loop) — that is what keeps the cost at n + 1 searches.
        after_lines = analysed(i + 1)

        before_cp, mate_before = to_mover_pov(before_lines[0]["score"], mover)
        # "After" has the opponent to move -> convert to the mover's POV.
        # Terminal positions are not sent to the engine: a delivered checkmate is
        # the best possible outcome for the mover (max), any other game-over is a
        # draw. See analyzer/terminal.py.
        terminal = terminal_eval(after_board, mover)
        if terminal is not None:
            after_cp, mate_after = terminal
        else:
            after_cp, mate_after = to_mover_pov(after_lines[0]["score"], mover)

        drop = win_pct(before_cp) - win_pct(after_cp)

        # Where did the played move rank among the MultiPV lines?
        rank: int | None = None
        for idx, line in enumerate(before_lines):
            pv = line.get("pv") or []
            if pv and pv[0] == mv:
                rank = idx + 1
                break

        # Precedence ladder (winpct.judge): forced -> best/~=best -> thresholds.
        # Rank 2-3 is deliberately NOT a suppression reason — the second-best
        # line can be far worse than the first, hiding real blunders (see plan.md).
        forced = pos.legal_moves.count() == 1
        verdict = judge(
            drop,
            forced=forced,
            rank=rank,
            thresholds=thresholds,
            suppress=suppress,
        )

        # Precedence #1: informational mate tags. Recorded as tags, but they do
        # NOT suppress a win%-threshold classification — walking into a forced
        # mate from a playable position is a real blunder. Forced moves get no
        # tag: there was no choice to make.
        tags: list[str] = []
        if not forced:
            if mate_before is not None and mate_before > 0 and not (
                mate_after is not None and mate_after > 0
            ):
                tags.append("missed_mate")
            if mate_after is not None and mate_after < 0:
                tags.append("allowed_mate")
            # Rule tags (B6) annotate a **flagged** move only. A tag explains
            # *why* a move was bad, and their predicates are deliberately loose
            # (a pawn left en prise counts, a free capture counts), so letting
            # them fire on "ok" moves — including mating ones — would bury the
            # report in noise and make the tool look wrong.
            if verdict.classification != "ok":
                if hanging_piece(pos, mv):
                    tags.append("hanging_piece")
                if missed_capture(pos, mv):
                    tags.append("missed_capture")
                if fork(pos, mv):
                    tags.append("fork")
                if pin(pos, mv):
                    tags.append("pin")

        played_san = pos.copy().san(mv)
        best_move = before_lines[0]["pv"][0]
        best_san = pos.copy().san(best_move)

        pv_san: list[str] = []
        pv_board = pos.copy()
        for pv_move in (before_lines[0].get("pv") or [])[:6]:
            pv_san.append(pv_board.san(pv_move))
            pv_board.push(pv_move)

        report = MoveReport(
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
            classification=verdict.classification,
            played_rank=rank,
            suppressed=verdict.suppressed,
            suppress_reason=verdict.reason,
            tags=tags,
            pv_san=pv_san,
        )

        # Position i+1 is ply i+1's "before" as well as ply i's "after" — search
        # it here, once, and hand it to the next iteration. This is what keeps
        # the cost at n + 1 searches despite the loop being lazy.
        before_lines = after_lines
        yield i, total, report


def analyse_moves(
    pgn_text: str,
    *,
    engine: Engine,
    depth: int | None = None,
    multipv: int | None = None,
    cache: PositionCache | None = None,
    per_move_timeout: float | None = None,
) -> list[MoveReport]:
    """Convenience: parse the first game in ``pgn_text`` and analyse its moves."""
    return analyse_game_moves(
        read_game(pgn_text), engine=engine, depth=depth, multipv=multipv, cache=cache, per_move_timeout=per_move_timeout
    )
