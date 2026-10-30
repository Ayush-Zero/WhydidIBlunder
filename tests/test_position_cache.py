"""D4 — durable cache correctness: the key definition, enforced by SQLite.

``tests/test_cache.py`` pins the key logic against the in-memory backend;
``tests/test_cache_wiring.py`` proves the pipeline consults it. This file covers
the remaining backend — the one the **worker** actually uses, where a wrong key
survives process restarts in ``position_eval``.

The four properties D4 names, exercised through ``SqlitePositionCache``:

* **FEN normalisation** — the fullmove counter is stripped, the halfmove clock is
  kept, and the row is stored under the *inherited* ``fen_key`` (not a
  reimplementation in the storage layer);
* **engine-version key** — a Stockfish bump is a miss, never a plausible answer
  computed by the old binary;
* **depth-≥ rule** — a shallow row cannot settle a deeper request; a deeper row
  can settle a shallower one and reports the depth actually used;
* **poisoning** — concurrent/duplicate stores cannot corrupt a row, and a key
  schema bump ignores old rows instead of reinterpreting them.

Offline: a throwaway SQLite file and hand-built engine output, no Stockfish.
"""

from __future__ import annotations

import chess
import chess.engine
import pytest

from analyzer.cache import CACHE_KEY_VERSION, PositionCache
from server import db
from server.position_cache import SqlitePositionCache, lines_from_json

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
START_LATER = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 37"
START_CLOCK = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 12 1"
AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"

ENGINE_A = "Stockfish 16"
ENGINE_B = "Stockfish 19"


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "cache.db")
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture()
def cache(conn):
    return SqlitePositionCache(conn)


def _lines(board: chess.Board, cp: int = 20) -> list:
    """Real engine-shaped output: a score from the side to move, plus a PV."""
    return [
        {
            "score": chess.engine.PovScore(chess.engine.Cp(cp), board.turn),
            "pv": [next(iter(board.legal_moves))],
            "depth": 0,
        }
    ]


def _store(cache, conn, *, fen=START, board=None, engine=ENGINE_B, depth=12, multipv=4, cp=20):
    board = board or chess.Board(fen)
    cache.store(
        fen=fen,
        engine_version=engine,
        depth=depth,
        multipv=multipv,
        lines=_lines(board, cp),
        board=board,
    )


def _lookup(cache, *, fen=START, board=None, engine=ENGINE_B, depth=12, multipv=4):
    board = board or chess.Board(fen)
    return cache.lookup(
        fen=fen,
        engine_version=engine,
        depth=depth,
        multipv=multipv,
        board=board,
    )


def _rows(conn) -> list:
    return conn.execute(
        "SELECT * FROM position_eval ORDER BY depth, engine_version"
    ).fetchall()


# --- the key the database actually uses -----------------------------------


def test_the_row_is_stored_under_the_inherited_fen_key(cache, conn):
    """The storage layer must not invent its own normalisation."""
    _store(cache, conn)

    row = _rows(conn)[0]
    assert row["fen_key"] == PositionCache.fen_key(START)
    assert row["fen_key"].startswith(f"v{CACHE_KEY_VERSION}:")
    # ``fen_full`` is the unnormalised position, kept only for debugging.
    assert row["fen_full"] == chess.Board(START).fen()


def test_fullmove_counter_is_stripped_through_the_database(cache, conn):
    """A board reached at move 37 reuses the move-1 row: evaluations do not
    depend on the move number, and keeping it would fragment the cache by ply."""
    _store(cache, conn, fen=START)

    hit = _lookup(cache, fen=START_LATER, board=chess.Board(START_LATER))
    assert hit is not None


def test_halfmove_clock_is_kept_through_the_database(cache, conn):
    """A clock of 12 is a different question from a clock of 0: the 50-move rule
    is real input near the horizon, so the rows must not share an entry."""
    _store(cache, conn, fen=START)

    assert _lookup(cache, fen=START_CLOCK, board=chess.Board(START_CLOCK)) is None
    assert len(_rows(conn)) == 1


def test_different_positions_do_not_collide_in_the_table(cache, conn):
    _store(cache, conn, fen=START)
    _store(cache, conn, fen=AFTER_E4, board=chess.Board(AFTER_E4))

    assert _lookup(cache, fen=AFTER_E4, board=chess.Board(AFTER_E4)) is not None
    assert len(_rows(conn)) == 2


# --- engine version: a bump is a miss, not a wrong answer -----------------


def test_an_engine_bump_does_not_serve_the_old_binary(cache, conn):
    """The poisoning property, durable: a plausible eval from a different build
    must never be returned. The old row survives for inspection."""
    _store(cache, conn, engine=ENGINE_A)

    assert _lookup(cache, engine=ENGINE_B) is None
    assert _lookup(cache, engine=ENGINE_A) is not None
    assert len(_rows(conn)) == 1


def test_engine_versions_coexist_without_cross_talk(cache, conn):
    _store(cache, conn, engine=ENGINE_A, cp=10)
    _store(cache, conn, engine=ENGINE_B, cp=90)

    low = _lookup(cache, engine=ENGINE_A)
    high = _lookup(cache, engine=ENGINE_B)
    assert low is not None and high is not None
    assert lines_from_json(_rows(conn)[0]["evals_json"])[0]["score"].relative.score() == 10
    assert lines_from_json(_rows(conn)[1]["evals_json"])[0]["score"].relative.score() == 90


def test_a_key_schema_bump_ignores_old_rows(conn, cache, monkeypatch):
    """``CACHE_KEY_VERSION`` is inside ``fen_key``: bumping it starts a new key
    space, so old rows are missed rather than reinterpreted."""
    _store(cache, conn)

    monkeypatch.setattr("analyzer.cache.CACHE_KEY_VERSION", CACHE_KEY_VERSION + 1)
    assert _lookup(cache, board=chess.Board(START)) is None
    assert len(_rows(conn)) == 1, "the old row is ignored, not deleted"
    assert _rows(conn)[0]["fen_key"].startswith(f"v{CACHE_KEY_VERSION}:")


# --- depth: deeper settles shallower, never the reverse -------------------


def test_a_shallow_row_does_not_settle_a_deeper_request(cache, conn):
    _store(cache, conn, depth=8)

    assert _lookup(cache, depth=20) is None
    hit = _lookup(cache, depth=8)
    assert hit is not None and hit[0] == 8


def test_a_deeper_row_settles_a_shallower_request_and_reports_its_depth(cache, conn):
    _store(cache, conn, depth=20, cp=33)

    stored_depth, _ = _lookup(cache, depth=12)
    assert stored_depth == 20, "the report must not claim it ran at depth 12"
    assert lines_from_json(_rows(conn)[0]["evals_json"])[0]["score"].relative.score() == 33


def test_a_late_shallow_search_does_not_downgrade_a_deeper_row(cache, conn):
    """Depth is part of the primary key, so the shallow result is a *new row*,
    not an overwrite; the inherited lookup still prefers the deep one."""
    _store(cache, conn, depth=20)
    _store(cache, conn, depth=8)

    assert [row["depth"] for row in _rows(conn)] == [8, 20]
    assert _lookup(cache, depth=12)[0] == 20


def test_the_deepest_row_that_clears_the_bar_wins(cache, conn):
    _store(cache, conn, depth=12)
    _store(cache, conn, depth=16)
    _store(cache, conn, depth=20)

    assert _lookup(cache, depth=10)[0] == 20


# --- multipv is part of the question --------------------------------------


def test_multipv_must_match_exactly_in_the_table(cache, conn):
    _store(cache, conn, multipv=4)

    assert _lookup(cache, multipv=1) is None
    assert _lookup(cache, multipv=2) is None
    assert _lookup(cache, multipv=4) is not None


# --- duplicate stores ------------------------------------------------------


def test_storing_the_same_key_twice_updates_one_row(cache, conn):
    """The upsert is one statement: a re-search must not create a second row."""
    _store(cache, conn, depth=12, cp=10)
    _store(cache, conn, depth=12, cp=90)

    rows = _rows(conn)
    assert len(rows) == 1
    assert lines_from_json(rows[0]["evals_json"])[0]["score"].relative.score() == 90
