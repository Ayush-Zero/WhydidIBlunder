"""D1: server-side grading for "try again".

The browser may send a SAN move, but it never sends a FEN, depth, MultiPV or an
evaluation. This module reconstructs the position from the stored report, checks
that the move is legal there, evaluates **only the resulting position** at the
report's depth/MultiPV, and compares the result with the report's before-eval.

Why one search and not two
--------------------------
The report already contains the engine's evaluation of the position *before* the
played move. Re-using it is both cheaper and more consistent: the candidate is
graded against the same baseline the report used, while the after-position is
the only new fact the engine has to produce. The accepted-risk is engine drift:
the after-position is searched with today's build, so a build change can move the
drop. That is recorded in ``opencode/phase3_breakdown.md``; the cache is keyed by
engine version, so an old row is never silently mixed with a new binary.

The implementation deliberately keeps the HTTP surface out of this file. The
API maps the exceptions below onto status codes, and tests can exercise the
grader with a fake engine without booting FastAPI.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass

import chess
import chess.engine

from analyzer.cache import PositionCache
from analyzer.config import (
    analysis_thresholds,
    suppression_win_pts,
    try_timeout_seconds,
)
from analyzer.engine import Engine, EngineTimeoutError
from analyzer.pipeline import _analyse_cached
from analyzer.pov import to_mover_pov
from analyzer.terminal import terminal_eval
from analyzer.winpct import judge, win_pct
from server.positions import replay_report

__all__ = [
    "PreparedTry",
    "TryBusyError",
    "TryEngineError",
    "TryGrader",
    "TryPositionError",
    "TryReportError",
    "TryResult",
    "default_engine_factory",
    "prepare_try",
]


class TryPositionError(ValueError):
    """The requested ply or candidate SAN is not valid for the report (422)."""


class TryReportError(ValueError):
    """The stored report cannot supply the grading baseline (409)."""


class TryBusyError(RuntimeError):
    """Another try evaluation owns the process-wide engine slot (503)."""


class TryEngineError(RuntimeError):
    """The engine failed, timed out, or returned no usable line (503)."""


@dataclass(frozen=True)
class PreparedTry:
    """A validated candidate, ready to be evaluated.

    The board is the position *before* the candidate. ``mover`` is the side that
    chose the candidate, and ``best_move`` is the report's best move in that
    same position.
    """

    board: chess.Board
    candidate: chess.Move
    mover: chess.Color
    best_move: chess.Move
    best_san: str
    pv_san: list[str]
    before_win_pct: float
    depth: int
    multipv: int
    forced: bool


@dataclass(frozen=True)
class TryResult:
    """The HTTP-neutral grading result."""

    accepted: bool
    classification: str
    drop_win_pct: float
    eval_after_cp: int
    best_san: str
    pv_san: list[str]
    threshold_win_pct: float
    reason: str


def _int_field(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TryReportError(f"stored report has no usable {name}")
    return value


def _win_pct_field(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TryReportError("stored report has no usable eval_before_win_pct")
    win = float(value)
    if not 0.0 <= win <= 100.0:
        raise TryReportError("stored report has an invalid eval_before_win_pct")
    return win


def prepare_try(
    report: object,
    *,
    user_color: str,
    ply: int,
    move_san: str,
) -> PreparedTry:
    """Reconstruct and validate the candidate from a stored report.

    Raises :class:`~server.positions.ReportReplayError` when the report itself
    cannot be replayed, :class:`TryPositionError` for a bad ply/colour/illegal
    move, and :class:`TryReportError` when the stored baseline is incomplete.
    """
    replay = replay_report(report)
    if ply < 1 or ply > len(replay.positions):
        raise TryPositionError(
            f"ply {ply} is outside this report (1-{len(replay.positions)})"
        )

    # ``replay_report`` guarantees this shape and the ply ordering.
    stored = report["moves"][ply - 1]  # type: ignore[index]
    if stored.get("side") != user_color:
        raise TryPositionError(
            f"ply {ply} belongs to {stored.get('side')!r}, not {user_color!r}"
        )

    board = chess.Board(replay.positions[ply - 1].fen_before)
    mover = board.turn
    try:
        candidate = board.parse_san(move_san)
    except ValueError as exc:
        raise TryPositionError(
            f"{move_san!r} is not a legal move in this position"
        ) from exc

    best_san = stored.get("best_san")
    if not isinstance(best_san, str) or not best_san:
        raise TryReportError(f"stored report has no best move for ply {ply}")
    try:
        best_move = board.parse_san(best_san)
    except ValueError as exc:
        raise TryReportError(
            f"stored best move {best_san!r} is not legal for ply {ply}"
        ) from exc

    pv_san = stored.get("pv_san") or []
    if not isinstance(pv_san, list) or not all(
        isinstance(line, str) for line in pv_san
    ):
        raise TryReportError(f"stored report has no usable line for ply {ply}")

    before_win_pct = _win_pct_field(stored.get("eval_before_win_pct"))

    engine = report.get("engine") if isinstance(report, dict) else None
    if not isinstance(engine, dict):
        raise TryReportError("stored report has no engine header")
    depth = _int_field(engine.get("depth"), "engine.depth")
    multipv = _int_field(engine.get("multipv"), "engine.multipv")
    if depth < 1 or multipv < 1:
        raise TryReportError("stored report has an invalid engine depth/MultiPV")

    return PreparedTry(
        board=board,
        candidate=candidate,
        mover=mover,
        best_move=best_move,
        best_san=best_san,
        pv_san=list(pv_san),
        before_win_pct=before_win_pct,
        depth=depth,
        multipv=multipv,
        forced=board.legal_moves.count() == 1,
    )


def default_engine_factory() -> Engine:
    """Build the same engine the worker uses, from server configuration."""
    from analyzer import config

    return Engine(
        config.stockfish_path(),
        threads=config.stockfish_threads(),
        hash_mb=config.stockfish_hash_mb(),
    )


class TryGrader:
    """Evaluate candidates with one process-wide engine slot.

    The slot is non-blocking on purpose: the API is not a second worker queue. If
    an evaluation is already running, the caller gets a truthful ``503`` (busy)
    instead of an unbounded wait. The timeout is passed to the engine call so a
    pathological search cannot hold the slot forever.
    """

    def __init__(
        self,
        *,
        engine_factory: Callable[[], object],
        cache_factory: Callable[[sqlite3.Connection], PositionCache],
        timeout_seconds: float | None = None,
    ) -> None:
        self._engine_factory = engine_factory
        self._cache_factory = cache_factory
        self._timeout_seconds = timeout_seconds
        self._slot = threading.BoundedSemaphore(1)

    def evaluate(
        self, connection: sqlite3.Connection, prepared: PreparedTry
    ) -> TryResult:
        """Run one candidate evaluation, or raise a mapped ``TryError``."""
        if not self._slot.acquire(blocking=False):
            raise TryBusyError("another try-again evaluation is already running")

        engine = None
        try:
            engine = self._engine_factory()
            cache = self._cache_factory(connection)
            return self._evaluate(engine, cache, prepared)
        except EngineTimeoutError as exc:
            raise TryEngineError("try-again evaluation timed out") from exc
        except (OSError, chess.engine.EngineError) as exc:
            raise TryEngineError("the try-again engine is unavailable") from exc
        finally:
            if engine is not None:
                close = getattr(engine, "close", None)
                if callable(close):
                    close()
            self._slot.release()

    def _evaluate(
        self,
        engine: object,
        cache: PositionCache,
        prepared: PreparedTry,
    ) -> TryResult:
        after = prepared.board.copy()
        after.push(prepared.candidate)

        terminal = terminal_eval(after, prepared.mover)
        if terminal is not None:
            after_cp, _ = terminal
        else:
            timeout = (
                try_timeout_seconds()
                if self._timeout_seconds is None
                else self._timeout_seconds
            )
            lines = _analyse_cached(
                engine,  # type: ignore[arg-type]
                after,
                prepared.depth,
                prepared.multipv,
                cache,
                timeout=timeout,
            )
            if not lines or not isinstance(lines[0], dict):
                raise TryEngineError("the engine returned no evaluation")
            score = lines[0].get("score")
            if score is None:
                raise TryEngineError("the engine returned no score")
            after_cp, _ = to_mover_pov(score, prepared.mover)

        drop = prepared.before_win_pct - win_pct(after_cp)
        thresholds = analysis_thresholds()
        suppress = suppression_win_pts()
        verdict = judge(
            drop,
            forced=prepared.forced,
            rank=1 if prepared.candidate == prepared.best_move else None,
            thresholds=thresholds,
            suppress=suppress,
        )

        accepted = verdict.classification == "ok"
        if accepted:
            if verdict.reason == "best move":
                reason = "the engine's best move"
            elif verdict.reason == "forced":
                reason = "the position has only one legal move"
            elif verdict.reason == "~= best":
                reason = (
                    f"within {suppress:g} win%-points of the position's best line"
                )
            else:
                reason = (
                    f"{drop:.1f} win%-points is below the "
                    f"{thresholds.inaccuracy:g} win%-point inaccuracy threshold"
                )
        else:
            reason = (
                f"{drop:.1f} win%-points worse than the position before the move"
            )

        return TryResult(
            accepted=accepted,
            classification=verdict.classification,
            drop_win_pct=drop,
            eval_after_cp=after_cp,
            best_san=prepared.best_san,
            pv_san=prepared.pv_san,
            threshold_win_pct=thresholds.inaccuracy,
            reason=reason,
        )
