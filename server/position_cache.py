"""The SQLite-backed position cache — B1's key, made durable.

``analyzer/cache.py`` owns *what a cache is*: the key, the depth rule, the hit
semantics, and ``CACHE_KEY_VERSION``. This module is only the storage half — it
persists entries in the ``position_eval`` table so a position analysed once is
never analysed again, across jobs and across worker restarts.

**Why ``_store_lines``/``_load_lines`` and not ``_store``/``_load``.** The base
class's storage hooks traffic in an opaque ``value``. A durable backend needs
something an in-memory one does not: the **board**, because a score's point of
view is only recoverable from the side to move. So the base class exposes the
line-aware hooks (see :meth:`analyzer.cache.PositionCache._store_lines`) and this
subclass implements those. The depth rule, the key construction and the "deepest
row at or above the request" selection are all inherited, unmodified — there is
exactly one definition of a cache hit, and it is not in this file.

**What is serialised.** The MultiPV line list, projected to plain JSON. A
:class:`chess.engine.Info` object is not JSON-serialisable (it holds engine
objects) and would make a cache row unreadable outside python-chess. The
projection stores the *raw* score with the side to move it was relative to, plus
the PV as UCI moves — exactly what
:func:`analyzer.pov.to_mover_pov` needs to re-anchor it.

**Atomicity.** ``INSERT … ON CONFLICT DO UPDATE`` is one statement, so two
workers analysing the same opening cannot clobber each other (openings repeat, so
this is the common case, not the exotic one). The ``WHERE excluded.depth >=
position_eval.depth`` guard keeps the durable half of the depth rule: a later
*shallower* re-search can never downgrade a stored deeper result.
"""

from __future__ import annotations

import json
import sqlite3

import chess
import chess.engine

from analyzer.cache import PositionCache
from server.db import utcnow

__all__ = ["SqlitePositionCache", "lines_from_json", "lines_to_json"]


def lines_to_json(lines: list, board: chess.Board) -> str:
    """Project engine output into JSON that reproduces what the pipeline reads.

    ``board`` supplies the side to move, the POV each ``PovScore`` is anchored
    to. Storing it means the score can be re-anchored on the way out rather than
    guessed.
    """
    turn = "white" if board.turn == chess.WHITE else "black"
    projected = []
    for info in lines:
        score = info["score"]
        entry: dict = {"depth": int(info.get("depth") or 0), "turn": turn}
        if score.is_mate():
            entry["mate"] = score.relative.mate()
        else:
            entry["cp"] = score.relative.score()
        entry["pv"] = [move.uci() for move in (info.get("pv") or [])]
        projected.append(entry)
    return json.dumps(projected)


def lines_from_json(payload: str) -> list:
    """Rebuild the pipeline's line shape from a stored row.

    The returned mappings are what the pipeline already treats these as — it
    reads only ``info["score"]`` and ``info["pv"]`` — with the score rebuilt as a
    :class:`chess.engine.PovScore` relative to the stored side to move, so
    :func:`analyzer.pov.to_mover_pov` sees exactly what a fresh search produced.
    """
    lines = []
    for entry in json.loads(payload):
        turn = chess.WHITE if entry.get("turn", "white") == "white" else chess.BLACK
        if entry.get("mate") is not None:
            score = chess.engine.PovScore(chess.engine.Mate(int(entry["mate"])), turn)
        else:
            score = chess.engine.PovScore(chess.engine.Cp(int(entry.get("cp", 0))), turn)
        pv = [chess.Move.from_uci(uci) for uci in entry.get("pv", [])]
        lines.append({"score": score, "pv": pv, "depth": int(entry.get("depth", 0))})
    return lines


class SqlitePositionCache(PositionCache):
    """Persists cache entries in ``position_eval``, keyed exactly as B1 says.

    ``conn`` is borrowed, not owned: the caller opened it and is responsible for
    closing it, because the same connection is doing the job bookkeeping and one
    connection keeps the transaction story coherent.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__()
        self._conn = conn

    # -- the line-aware storage hooks -----------------------------------
    def _store_lines(
        self, fen_key, engine_version, depth, multipv, lines, board
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO position_eval
                (fen_key, engine_version, depth, multipv, fen_full, evals_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fen_key, engine_version, depth, multipv) DO UPDATE
                SET evals_json = excluded.evals_json
              WHERE excluded.depth >= position_eval.depth
            """,
            (
                fen_key,
                engine_version,
                int(depth),
                int(multipv),
                board.fen(),
                lines_to_json(lines, board),
                utcnow(),
            ),
        )

    def _load_lines(self, fen_key, engine_version, board):
        rows = self._conn.execute(
            """
            SELECT depth, multipv, evals_json
              FROM position_eval
             WHERE fen_key = ? AND engine_version = ?
            """,
            (fen_key, engine_version),
        ).fetchall()
        if not rows:
            return None
        by_depth: dict[int, dict[int, list]] = {}
        for row in rows:
            by_depth.setdefault(int(row["depth"]), {})[int(row["multipv"])] = (
                lines_from_json(row["evals_json"])
            )
        return by_depth

    # ``_store``/``_load`` are unused: this backend's values are JSON text that
    # only ``_store_lines``/``_load_lines`` know how to build. Overridden to
    # raise so a mistaken call fails loudly instead of silently storing an
    # unserialisable object.
    def _store(self, *args, **kwargs):  # pragma: no cover - not this backend's path
        raise NotImplementedError("SqlitePositionCache uses _store_lines/_load_lines")

    def _load(self, *args, **kwargs):  # pragma: no cover - not this backend's path
        raise NotImplementedError("SqlitePositionCache uses _store_lines/_load_lines")
