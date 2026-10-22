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
) -> list:
    """``engine.analyse`` with the B1 cache in front of it.

    A cache *hit at a deeper depth than requested* is returned as-is rather than
    truncated, because more search is strictly better — the caller only reads the
    lines, and the depth actually used is recorded in the report's engine header
    by :mod:`analyzer.report`.
    """
    if cache is None:
        return engine.analyse(board, depth=depth, multipv=multipv)

    hit = cache.lookup(
        fen=board.fen(),
        engine_version=PositionCache.engine_version(engine),
        depth=depth,
        multipv=multipv,
    )
    if hit is not None:
        return hit[1]

    lines = engine.analyse(board, depth=depth, multipv=multipv)
    cache.store(
        fen=board.fen(),
        engine_version=PositionCache.engine_version(engine),
        depth=depth,
        multipv=multipv,
        value=lines,
    )
    return lines


def analyse_game_moves(
    game: chess.pgn.Game,
    *,
    engine: Engine,
    depth: int | None = None,
    multipv: int | None = None,
    cache: PositionCache | None = None,
) -> list[MoveReport]:
    """Analyse every move of ``game``.

    Each position is searched once and reused as the next move's "before"
    (halves the work). Classification is from the mover's perspective.

    ``depth``/``multipv`` default to the configured values
    (``ANALYSIS_DEPTH`` / ``ANALYSIS_MULTIPV``) — they are never hardcoded here.

    ``cache`` (B1) is consulted before every engine call and filled after it, so
    a repeated position — across games, not just within one — is searched once.
    ``None`` means "no cache" and is the default: caching is a correctness-bearing
    optimisation, so it is switched on deliberately (``CACHE_DIR``) rather than
    inherited. ``tests/test_cache_wiring.py`` pins both the hit path and the
    fact that an engine bump invalidates it.
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

    # positions 0..n, each analysed exactly once; terminal positions are skipped
    analyses = [
        None if b.is_game_over() else _analyse_cached(engine, b, depth, multipv, cache)
        for b in boards
    ]

    reports: list[MoveReport] = []
    for i, mv in enumerate(moves):
        pos = boards[i]
        mover = pos.turn

        before_lines = analyses[i]
        after_board = boards[i + 1]

        before_cp, mate_before = to_mover_pov(before_lines[0]["score"], mover)
        # "After" has the opponent to move -> convert to the mover's POV.
        # Terminal positions are not sent to the engine: a delivered checkmate is
        # the best possible outcome for the mover (max), any other game-over is a
        # draw. See analyzer/terminal.py.
        terminal = terminal_eval(after_board, mover)
        if terminal is not None:
            after_cp, mate_after = terminal
        else:
            after_cp, mate_after = to_mover_pov(analyses[i + 1][0]["score"], mover)

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
                classification=verdict.classification,
                played_rank=rank,
                suppressed=verdict.suppressed,
                suppress_reason=verdict.reason,
                tags=tags,
                pv_san=pv_san,
            )
        )

    return reports


def analyse_moves(
    pgn_text: str,
    *,
    engine: Engine,
    depth: int | None = None,
    multipv: int | None = None,
    cache: PositionCache | None = None,
) -> list[MoveReport]:
    """Convenience: parse the first game in ``pgn_text`` and analyse its moves."""
    return analyse_game_moves(
        read_game(pgn_text), engine=engine, depth=depth, multipv=multipv, cache=cache
    )
