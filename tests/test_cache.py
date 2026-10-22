"""B1 — the position cache: key shape, depth rule, and poisoning (offline).

The cache is the sneakiest bug class in Phase 2: a wrong key does not crash, it
serves the wrong evaluation forever. So these tests are deliberately about
*failure modes* rather than the happy path:

* the FEN key must ignore the fullmove counter but **not** the halfmove clock;
* a shallower stored row must never answer a deeper request;
* an engine bump must invalidate everything, silently but safely;
* MultiPV=1 and MultiPV=4 are different questions, not prefixes of each other.

Nothing here touches a real engine or a database — the cache is exercised through
its in-memory backend, which shares all the key logic with the SQLite one.
"""

from __future__ import annotations

import chess
import pytest

from analyzer.cache import (
    CACHE_FEN_SCHEMA,
    CACHE_KEY_VERSION,
    MemoryPositionCache,
    NullPositionCache,
    PositionCache,
)

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
START_LATER = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 37"
START_CLOCK = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 12 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"


@pytest.fixture()
def cache():
    return MemoryPositionCache()


# --- FEN normalisation ----------------------------------------------------


def test_fen_schema_is_stated_explicitly():
    """The mask is a fact about the code, not a remembered decision."""
    assert CACHE_FEN_SCHEMA == "board turn castling ep halfmove"


def test_fullmove_counter_is_stripped():
    """Nothing about a position's evaluation depends on the move number, so
    keeping it would fragment the cache once per ply."""
    assert PositionCache.normalize_fen(START) == PositionCache.normalize_fen(START_LATER)


def test_halfmove_clock_is_kept():
    """The 50-move rule is a real input near the horizon, so two positions
    differing only in the clock must NOT share a cache entry."""
    assert PositionCache.normalize_fen(START) != PositionCache.normalize_fen(START_CLOCK)
    assert PositionCache.normalize_fen(START).endswith(" 0")
    assert PositionCache.normalize_fen(START_CLOCK).endswith(" 12")


def test_different_positions_do_not_collide():
    assert PositionCache.normalize_fen(START) != PositionCache.normalize_fen(AFTER_E4)


def test_normalisation_of_malformed_fen_does_not_raise():
    """Nonsense in must be a cache miss, not an exception from the cache layer."""
    assert PositionCache.normalize_fen("") == ""
    assert PositionCache.normalize_fen("x") == "x"
    # Four fields: not enough for a clock, so it is returned as-is.
    assert PositionCache.normalize_fen("8/8/8/8 w - -") == "8/8/8/8 w - -"


def test_fen_key_is_prefixed_with_the_key_version():
    """A change to the normalisation must invalidate old rows rather than
    reinterpreting them, so the version is part of the key itself."""
    assert PositionCache.fen_key(START).startswith(f"v{CACHE_KEY_VERSION}:")


def test_fen_key_is_stable_across_calls():
    assert PositionCache.fen_key(START) == PositionCache.fen_key(START)


# --- hit / miss -----------------------------------------------------------


def test_store_then_lookup_is_a_hit(cache):
    cache.store(fen=START, engine_version="Stockfish 19", depth=12, multipv=4, value="A")
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=4) == (
        12,
        "A",
    )


def test_lookup_reports_the_depth_that_was_actually_stored(cache):
    """A depth-20 row answering a depth-12 request is a hit, but the caller has
    to know it was 20 — the report must not claim it ran at 12."""
    cache.store(fen=START, engine_version="Stockfish 19", depth=20, multipv=4, value="deep")
    stored_depth, value = cache.lookup(
        fen=START, engine_version="Stockfish 19", depth=12, multipv=4
    )
    assert stored_depth == 20 and value == "deep"


def test_lookup_on_empty_cache_misses(cache):
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=4) is None


def test_lookup_of_a_different_position_misses(cache):
    cache.store(fen=START, engine_version="Stockfish 19", depth=12, multipv=4, value="A")
    assert cache.lookup(fen=AFTER_E4, engine_version="Stockfish 19", depth=12, multipv=4) is None


# --- the depth rule -------------------------------------------------------


def test_shallower_stored_row_does_not_answer_a_deeper_request(cache):
    """The core safety property: serving depth 8 for a depth-20 request would
    silently weaken the analysis while looking like a cache win."""
    cache.store(fen=START, engine_version="Stockfish 19", depth=8, multipv=4, value="shallow")
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=20, multipv=4) is None


def test_equal_depth_is_a_hit(cache):
    cache.store(fen=START, engine_version="Stockfish 19", depth=12, multipv=4, value="A")
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=4) is not None


def test_deeper_row_answers_a_shallower_request(cache):
    cache.store(fen=START, engine_version="Stockfish 19", depth=18, multipv=4, value="deep")
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=4) == (
        18,
        "deep",
    )


def test_the_deepest_available_row_wins(cache):
    """Given several rows that clear the bar, prefer the most thorough one."""
    for depth, value in ((12, "d12"), (16, "d16"), (20, "d20")):
        cache.store(fen=START, engine_version="Stockfish 19", depth=depth, multipv=4, value=value)
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=10, multipv=4) == (
        20,
        "d20",
    )


def test_depth_is_still_pinned_by_the_request(cache):
    """A depth-16 row must not be preferred over a depth-20 one for a depth-20
    request just because it was stored later."""
    cache.store(fen=START, engine_version="Stockfish 19", depth=20, multipv=4, value="d20")
    cache.store(fen=START, engine_version="Stockfish 19", depth=16, multipv=4, value="d16")
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=20, multipv=4) == (
        20,
        "d20",
    )


# --- multipv is part of the question --------------------------------------


def test_multipv_must_match_exactly(cache):
    """MultiPV=1 and MultiPV=4 return different first lines, so one is not a
    prefix of the other and they must not share an entry."""
    cache.store(fen=START, engine_version="Stockfish 19", depth=12, multipv=4, value="four")
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=1) is None
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=2) is None


def test_multipv_variants_coexist(cache):
    cache.store(fen=START, engine_version="Stockfish 19", depth=12, multipv=1, value="one")
    cache.store(fen=START, engine_version="Stockfish 19", depth=12, multipv=4, value="four")
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=1)[1] == "one"
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=4)[1] == "four"


# --- poisoning ------------------------------------------------------------


def test_engine_bump_invalidates_the_cache(cache):
    """The poisoning test. Different builds return different evaluations, so a
    new build must simply not match the old build's rows.

    Note the failure is *silent and safe*: the lookup misses and the position is
    re-searched. The dangerous alternative — a build-agnostic key — would return
    a plausible number computed by a different engine.
    """
    cache.store(fen=START, engine_version="Stockfish 16", depth=20, multipv=4, value="old")
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=4) is None
    # ...and the old row is still there, not corrupted.
    assert cache.lookup(fen=START, engine_version="Stockfish 16", depth=12, multipv=4) == (
        20,
        "old",
    )


def test_engine_versions_coexist_without_cross_talk(cache):
    cache.store(fen=START, engine_version="Stockfish 16", depth=12, multipv=4, value="v16")
    cache.store(fen=START, engine_version="Stockfish 19", depth=12, multipv=4, value="v19")
    assert cache.lookup(fen=START, engine_version="Stockfish 16", depth=12, multipv=4)[1] == "v16"
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=4)[1] == "v19"


def test_key_does_not_embed_the_engine_version_into_the_fen_key():
    """engine_version is a separate key *column*, so an old build's rows can
    still be inspected instead of being indistinguishable from the new ones."""
    assert "Stockfish" not in PositionCache.fen_key(START)


def test_storing_a_shallower_result_after_a_deeper_one_does_not_weaken_it(cache):
    """A late depth-8 result must not shadow the depth-20 row already present."""
    cache.store(fen=START, engine_version="Stockfish 19", depth=20, multipv=4, value="deep")
    cache.store(fen=START, engine_version="Stockfish 19", depth=8, multipv=4, value="late-shallow")
    assert cache.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=4) == (
        20,
        "deep",
    )


# --- engine version reading ----------------------------------------------


class _FakeEngine:
    """Minimal stand-in: the cache only ever reads ``name`` from it."""

    def __init__(self, name: str):
        self.name = name
        self.id = {"name": name}


def test_engine_version_is_read_from_the_engine_object():
    """Never from a CLI constant: the value in the key must come from the binary
    that actually answered."""
    assert PositionCache.engine_version(_FakeEngine("Stockfish 19")) == "Stockfish 19"


def test_engine_version_falls_back_to_the_id_dict():
    class OnlyId:
        id = {"name": "Stockfish 17"}

    assert PositionCache.engine_version(OnlyId()) == "Stockfish 17"


# --- the disabled cache ---------------------------------------------------


def test_null_cache_misses_and_remembers_nothing():
    """"No caching" is an explicit value, not ``None`` passed by accident."""
    null = NullPositionCache()
    null.store(fen=START, engine_version="Stockfish 19", depth=12, multipv=4, value="A")
    assert null.lookup(fen=START, engine_version="Stockfish 19", depth=12, multipv=4) is None


def test_memory_cache_round_trips_a_real_board():
    """Sanity: the key works on a FEN taken from an actual board."""
    board = chess.Board()
    board.push_san("e4")
    fen = board.fen()
    cache = MemoryPositionCache()
    cache.store(fen=fen, engine_version="Stockfish 19", depth=12, multipv=4, value=[1, 2, 3])
    assert cache.lookup(fen=fen, engine_version="Stockfish 19", depth=12, multipv=4)[1] == [1, 2, 3]


def test_memory_cache_len_counts_entries(cache):
    assert len(cache) == 0
    cache.store(fen=START, engine_version="Stockfish 19", depth=12, multipv=4, value="A")
    cache.store(fen=AFTER_E4, engine_version="Stockfish 19", depth=12, multipv=4, value="B")
    assert len(cache) == 2
