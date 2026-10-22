"""B1 wiring — the cache in front of the engine (offline, fake engine).

`tests/test_cache.py` proves the key logic in isolation. This file proves the
other half: that the pipeline actually *consults* it, that a hit really does skip
an engine call, and that a hit is not silently served across an engine bump.

The fake engine counts calls, so "the cache saved a search" is an assertion about
a number rather than an inference from timing.
"""

from __future__ import annotations

import chess
import chess.engine
import pytest

from analyzer.cache import MemoryPositionCache, PositionCache
from analyzer.pipeline import analyse_moves

# 4 plies: positions 0..4, so 5 searches without a cache.
PGN = """[Event "cache-wiring"]
[White "A"]
[Black "B"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 *
"""


class CountingEngine:
    """A fake engine that records every position it is asked about."""

    def __init__(self, name: str = "Fake Engine 1"):
        self.name = name
        self.calls: list[str] = []

    def analyse(self, board: chess.Board, depth: int, multipv: int) -> list:
        self.calls.append(board.fen())
        cp = 10 * board.fullmove_number + (0 if board.turn == chess.WHITE else 1)
        first = next(iter(board.legal_moves))
        return [
            {
                "score": chess.engine.PovScore(chess.engine.Cp(cp), board.turn),
                "pv": [first],
            }
        ]


def test_without_a_cache_every_position_is_searched():
    engine = CountingEngine()
    analyse_moves(PGN, engine=engine, depth=12, multipv=4)
    assert len(engine.calls) == 5


def test_a_second_pass_is_served_entirely_from_the_cache():
    """The point of the cache: the same game analysed twice costs one search set."""
    cache = MemoryPositionCache()
    first = CountingEngine()
    analyse_moves(PGN, engine=first, depth=12, multipv=4, cache=cache)
    assert len(first.calls) == 5

    second = CountingEngine()
    analyse_moves(PGN, engine=second, depth=12, multipv=4, cache=cache)
    assert second.calls == []


def test_cached_run_produces_an_identical_result():
    """A cache hit must be indistinguishable from a fresh search."""
    cache = MemoryPositionCache()
    fresh = analyse_moves(PGN, engine=CountingEngine(), depth=12, multipv=4)
    warm = analyse_moves(
        PGN, engine=CountingEngine(), depth=12, multipv=4, cache=cache
    )
    cached = analyse_moves(
        PGN, engine=CountingEngine(), depth=12, multipv=4, cache=cache
    )

    def shape(reports):
        return [
            (m.ply, m.played_san, m.eval_before_cp, m.eval_after_cp,
             m.drop, m.classification, m.suppressed)
            for m in reports
        ]

    assert shape(warm) == shape(fresh)
    assert shape(cached) == shape(fresh)


def test_positions_shared_between_games_are_only_searched_once():
    """The real win: openings repeat, so a second game reuses the first's work."""
    cache = MemoryPositionCache()
    game_a = CountingEngine()
    analyse_moves(PGN, engine=game_a, depth=12, multipv=4, cache=cache)

    # A game with the same opening but a different continuation.
    other = """[Event "cache-wiring-2"]
[White "A"]
[Black "B"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *
"""
    game_b = CountingEngine()
    analyse_moves(other, engine=game_b, depth=12, multipv=4, cache=cache)

    # Plies 1-4 are the same positions, so only the two new ones are searched.
    assert len(game_b.calls) == 2


def test_an_engine_bump_re_searches_everything():
    """Poisoning, end to end: a different engine must not be served the old
    engine's numbers, even though the positions are identical."""
    cache = MemoryPositionCache()
    old = CountingEngine("Stockfish 16")
    analyse_moves(PGN, engine=old, depth=12, multipv=4, cache=cache)

    new = CountingEngine("Stockfish 19")
    analyse_moves(PGN, engine=new, depth=12, multipv=4, cache=cache)
    assert len(new.calls) == 5


def test_a_shallower_stored_result_does_not_satisfy_a_deeper_request():
    """Wiring-level check of the depth rule: asking for more must search again."""
    cache = MemoryPositionCache()
    shallow = CountingEngine()
    analyse_moves(PGN, engine=shallow, depth=8, multipv=4, cache=cache)
    assert len(shallow.calls) == 5

    deeper = CountingEngine()
    analyse_moves(PGN, engine=deeper, depth=16, multipv=4, cache=cache)
    assert len(deeper.calls) == 5, "a depth-8 row must not answer a depth-16 request"


def test_a_deeper_stored_result_does_satisfy_a_shallower_request():
    cache = MemoryPositionCache()
    deep = CountingEngine()
    analyse_moves(PGN, engine=deep, depth=16, multipv=4, cache=cache)

    shallow = CountingEngine()
    analyse_moves(PGN, engine=shallow, depth=12, multipv=4, cache=cache)
    assert shallow.calls == [], "deeper than requested is strictly better"


def test_a_different_multipv_is_not_a_cache_hit():
    cache = MemoryPositionCache()
    four = CountingEngine()
    analyse_moves(PGN, engine=four, depth=12, multipv=4, cache=cache)

    one = CountingEngine()
    analyse_moves(PGN, engine=one, depth=12, multipv=1, cache=cache)
    assert len(one.calls) == 5


def test_the_cache_records_the_real_engine_name():
    """The key must be built from the engine that answered, not a constant."""
    cache = MemoryPositionCache()
    analyse_moves(PGN, engine=CountingEngine("Stockfish 99"), depth=12, multipv=4, cache=cache)
    versions = {version for (_, version) in cache._entries}
    assert versions == {"Stockfish 99"}


def test_no_cache_argument_means_no_caching():
    """The default stays uncached so a plain call can never serve stale evals."""
    engine = CountingEngine()
    analyse_moves(PGN, engine=engine, depth=12, multipv=4)
    engine2 = CountingEngine()
    analyse_moves(PGN, engine=engine2, depth=12, multipv=4)
    assert len(engine2.calls) == 5


def test_default_cache_follows_config(monkeypatch):
    """CACHE_DIR unset disables caching; setting it enables it."""
    from analyzer import report

    monkeypatch.delenv("CACHE_DIR", raising=False)
    assert report.default_cache() is None

    monkeypatch.setenv("CACHE_DIR", "./data/poscache")
    cache = report.default_cache()
    assert isinstance(cache, PositionCache)


def test_analyze_game_accepts_and_uses_a_cache(monkeypatch):
    """The public entry point threads the cache through to the move loop."""
    from analyzer.report import analyze_game

    cache = MemoryPositionCache()
    analyze_game(PGN, "white", engine=CountingEngine(), depth=12, multipv=4, cache=cache)
    assert len(cache) == 5

    reuse = CountingEngine()
    analyze_game(PGN, "white", engine=reuse, depth=12, multipv=4, cache=cache)
    assert reuse.calls == []
