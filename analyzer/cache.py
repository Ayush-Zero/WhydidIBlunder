"""The position cache — normalisation, keys, and the depth-aware store.

A wrong cache key does not crash; it serves the wrong evaluation forever. So the
key is assembled from exactly three things, each with a justification:

``fen_key``
    The position with the **fullmove counter stripped and the halfmove clock
    kept**.

    * Keeping the halfmove clock is the safe direction. The 50-move rule is a
      real input to an engine's evaluation near the horizon, so two positions
      differing only in the clock can legitimately be scored differently. A cache
      that ignores the clock can therefore serve a draw-ish score for a position
      the engine would have scored as a win.
    * Dropping the fullmove counter is the other safe direction: nothing about a
      position's evaluation depends on move number, and keeping it would
      fragment the cache once per ply, halving the hit rate for no benefit.

``engine_version``
    Stockfish's own id string (``"Stockfish 19"``). Different builds return
    different evaluations; mixing them silently corrupts the cache. This is a
    *key column* rather than part of ``fen_key`` so that a bump stops matching
    old rows instead of colliding with them.

``depth`` / ``multipv``
    Both are part of the key, and a stored row may answer a request for a
    **shallower** depth but never a deeper one (see :meth:`PositionCache.lookup`).

The same three facts are what ``server/db.py``'s ``position_eval`` table is
keyed on, so the constants here (``CACHE_FEN_SCHEMA``, ``CACHE_KEY_VERSION``)
are bumped whenever a stored row stops meaning what it used to.
"""

from __future__ import annotations

import chess
import chess.engine

__all__ = [
    "CACHE_FEN_SCHEMA",
    "CACHE_KEY_VERSION",
    "MemoryPositionCache",
    "NullPositionCache",
    "PositionCache",
]

#: Bump when the *meaning* of a stored row changes — e.g. a different FEN
#: normalisation or a different eval encoding. Rows are then simply ignored and
#: re-fetched, never reinterpreted.
CACHE_KEY_VERSION = 1

#: The field mask applied to a FEN. Stated explicitly, and asserted in the
#: tests, so "keep the clock, drop the move number" is a fact about the code and
#: not a memory of a decision.
CACHE_FEN_SCHEMA = "board turn castling ep halfmove"


class PositionCache:
    """Stores engine output per (position, engine, depth, multipv).

    An abstract class, not an interface backed by a file: the storage mechanism
    is deliberately left to a subclass (SQLite in Phase 2 B, an in-memory dict
    for tests), while the two things that must never differ — how a key is built
    and what counts as a hit — live here and are tested once.
    """

    #: ``{fen_key: {depth: {multipv: value}}}`` — a nested dict because the
    #: lookup is "deepest depth >= requested", which a flat key cannot answer
    #: without scanning.
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], dict[int, dict[int, object]]] = {}

    # -- keys ------------------------------------------------------------

    @staticmethod
    def normalize_fen(fen: str) -> str:
        """Strip the fullmove counter, keep the halfmove clock.

        A malformed FEN is returned verbatim (whitespace-collapsed) rather than
        raising: a caller should get a cache *miss* on nonsense input, not an
        exception from the cache layer.
        """
        fields = str(fen).split()
        if len(fields) < 4:
            return " ".join(fields)
        # board, turn, castling, en passant, halfmove clock — drop fullmove.
        return " ".join(fields[:5]) if len(fields) >= 5 else " ".join(fields[:4])

    @classmethod
    def fen_key(cls, fen: str) -> str:
        """The normalised position string, with the key schema version."""
        return f"v{CACHE_KEY_VERSION}:{cls.normalize_fen(fen)}"

    @staticmethod
    def multipv_key(multipv: int) -> int:
        """MultiPV = 1 and MultiPV = 4 return *different* first lines.

        With one line Stockfish reports the best move; with several it reports
        each line's own evaluation, and the two can differ. So the number of
        lines requested is part of the key rather than an assumption that one
        line is a prefix of the other.
        """
        return int(multipv)

    @staticmethod
    def engine_version(engine: chess.engine.SimpleEngine) -> str:
        """Stockfish's id string, e.g. ``"Stockfish 19"``.

        Read from the *engine object*, never from a CLI constant, so the value
        in the key is necessarily the binary that actually answered.
        """
        return str(getattr(engine, "name", None) or engine.id.get("name", "unknown"))

    # -- storage (subclass responsibility) -------------------------------

    def _store(self, fen_key: str, engine_version: str, depth: int, multipv: int, value) -> None:
        raise NotImplementedError

    def _load(self, fen_key: str, engine_version: str) -> dict[int, dict[int, object]] | None:
        raise NotImplementedError

    # -- the API ---------------------------------------------------------

    def lookup(
        self,
        *,
        fen: str,
        engine_version: str,
        depth: int,
        multipv: int,
    ) -> tuple[int, object] | None:
        """A cached result at **at least** ``depth``, or ``None`` on a miss.

        Returns ``(stored_depth, value)`` so the caller knows how deep the hit
        actually was — a depth-20 row answering a depth-12 request is a hit, but
        the report should not claim it ran at 12.

        The strictness matters in both directions: the depth must be ``>=`` the
        request (a shallower row is a *miss* — serving it would silently weaken
        the analysis), and the multipv must match exactly.
        """
        depth = int(depth)
        multipv = self.multipv_key(multipv)
        rows = self._load(self.fen_key(fen), engine_version)
        if not rows:
            return None

        candidates = [
            stored_depth
            for stored_depth, by_multipv in rows.items()
            if stored_depth >= depth and multipv in by_multipv
        ]
        if not candidates:
            return None

        # Deepest available: prefer the most thorough evaluation we already have
        # rather than one that merely clears the bar.
        stored_depth = max(candidates)
        return stored_depth, rows[stored_depth][multipv]

    def store(
        self,
        *,
        fen: str,
        engine_version: str,
        depth: int,
        multipv: int,
        value: object,
    ) -> None:
        """Record a result. Deeper results are never overwritten by shallower."""
        key = self.fen_key(fen)
        cols = self._load(key, engine_version) or {}
        by_depth = cols.setdefault(int(depth), {})
        by_depth[self.multipv_key(multipv)] = value
        self._store(key, engine_version, int(depth), self.multipv_key(multipv), value)


class MemoryPositionCache(PositionCache):
    """In-process cache. Default backend for tests and single-process runs."""

    def _store(self, fen_key, engine_version, depth, multipv, value) -> None:
        self._entries.setdefault((fen_key, engine_version), {}).setdefault(depth, {})[
            multipv
        ] = value

    def _load(self, fen_key, engine_version):
        return self._entries.get((fen_key, engine_version))

    def __len__(self) -> int:
        return sum(
            len(by_multipv) for cols in self._entries.values() for by_multipv in cols.values()
        )


class NullPositionCache(PositionCache):
    """Caching switched off: every lookup misses, nothing is remembered.

    Present so that "no caching" is a *value* that can be passed around
    explicitly, rather than ``None`` meaning both "no cache" and "forgot to pass
    one" at different call sites.
    """

    def _store(self, *args, **kwargs) -> None:
        return None

    def _load(self, *args, **kwargs):
        return None
