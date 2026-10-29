"""B3 — the worker loop: claim → analyse → persist → finalise (offline).

The worker is where B2's queue meets Phase 1's analyser, so these tests exercise
the *seams* rather than the analyser (which has its own tests):

* a job runs end to end and lands ``queued → running → done`` with a real report;
* every ply is persisted, and progress advances with it;
* a cancel between plies stops the job, and the plies already written stay;
* a failure leaves the job settled (``failed``), never stuck ``running``;
* the durable position cache round-trips through ``position_eval`` and saves a
  second job the searches.

No engine and no network: a fake engine returns legal moves, and the worker is
driven one job at a time via ``run_once`` so there is no sleeping process.
"""

from __future__ import annotations

import json

import chess
import chess.engine
import pytest

from server import db
from server import queue as jobq
from server.position_cache import SqlitePositionCache, lines_from_json, lines_to_json
from server.worker import Worker, default_cache_for, should_cancel

# 6 plies, no terminal position.
PGN = """[Event "b3-test"]
[White "BEAST-ASH"]
[Black "Rival"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *
"""


class FakeEngine:
    """A stand-in for Stockfish: legal moves, deterministic scores, call count."""

    def __init__(self, name: str = "Fake Engine 1"):
        self.name = name
        self.calls: list[str] = []
        self.closed = False

    def analyse(self, board: chess.Board, depth: int, multipv: int) -> list:
        self.calls.append(board.fen())
        cp = 20 if board.turn == chess.WHITE else -20
        score = chess.engine.PovScore(chess.engine.Cp(cp), board.turn)
        moves = list(board.legal_moves)
        lines = []
        for i in range(multipv):
            mv = moves[i % len(moves)]
            lines.append({"score": score, "pv": [mv], "depth": depth})
        return lines

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "worker.db")
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture()
def cache_off(monkeypatch):
    """The default state: no durable cache, so search counts are unaffected."""
    monkeypatch.delenv("CACHE_DIR", raising=False)


def _enqueue(conn, job_id="job-1", game_key="chesscom:abc", color="white"):
    return db.insert_job(
        conn,
        job_id=job_id,
        game_key=game_key,
        username="BEAST-ASH",
        user_color=color,
        pgn=PGN,
        depth=8,
        multipv=2,
    )


def _worker(engine=None, **kwargs):
    engine = engine or FakeEngine()
    return Worker(engine_factory=lambda: engine, sleep_seconds=0.0, **kwargs)


# --- the happy path -------------------------------------------------------


def test_a_job_runs_end_to_end(conn, cache_off):
    _enqueue(conn)
    result = _worker().run_once(conn)

    assert result["status"] == "done"
    assert result["plies"] == 6
    assert db.get_job(conn, "job-1")["status"] == "done"


def test_the_status_goes_queued_running_done(conn, cache_off):
    _enqueue(conn)
    assert db.get_job(conn, "job-1")["status"] == "queued"

    worker = _worker()
    seen: list[str] = []
    original = worker.process_job

    def spy(connection, job):
        seen.append(db.get_job(connection, job["id"])["status"])
        return original(connection, job)

    worker.process_job = spy
    worker.run_once(conn)

    assert seen == ["running"], "the job is running while it is processed"
    assert db.get_job(conn, "job-1")["status"] == "done"


def test_every_ply_is_persisted(conn, cache_off):
    _enqueue(conn)
    _worker().run_once(conn)

    rows = db.list_moves(conn, "job-1")
    assert [row["ply"] for row in rows] == [1, 2, 3, 4, 5, 6]
    assert [row["san"] for row in rows] == ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]
    assert rows[0]["side"] == "white"
    assert rows[1]["side"] == "black"


def test_progress_reaches_the_full_ply_count(conn, cache_off):
    _enqueue(conn)
    _worker().run_once(conn)
    assert db.get_job(conn, "job-1")["progress_ply"] == 6


def test_the_report_is_stored_and_valid(conn, cache_off):
    _enqueue(conn)
    _worker().run_once(conn)

    row = db.get_report(conn, "chesscom:abc", "white")
    assert row is not None
    report = json.loads(row["report_json"])
    assert report["schema_version"] == 1
    assert report["game"]["white"] == "BEAST-ASH"
    assert report["summary"]["user_color"] == "white"
    assert row["moves_analysed"] == 3  # white's three moves
    assert row["engine_version"] == "Fake Engine 1"


def test_the_fens_written_are_the_real_neighbours(conn, cache_off):
    """``fen_after`` of ply n must equal ``fen_before`` of ply n+1, or the stored
    positions are not a game."""
    _enqueue(conn)
    _worker().run_once(conn)
    rows = db.list_moves(conn, "job-1")
    for current, following in zip(rows, rows[1:]):
        assert current["fen_after"] == following["fen_before"]


def test_reanalysing_a_ply_upserts_rather_than_crashing(conn, cache_off):
    """A retried job re-writes plies it already wrote; that must be idempotent."""
    _enqueue(conn)
    _worker().run_once(conn)
    jobq.requeue_job(conn, "job-1")
    _worker().run_once(conn)
    assert len(db.list_moves(conn, "job-1")) == 6


def test_running_an_empty_queue_does_nothing(conn, cache_off):
    assert _worker().run_once(conn) is None


def test_the_engine_is_closed_even_on_failure(conn, cache_off, monkeypatch):
    """A leaked Stockfish process per failed job would eat the machine."""
    _enqueue(conn)
    engine = FakeEngine()

    def explode(*args, **kwargs):
        raise RuntimeError("engine died")

    monkeypatch.setattr("server.worker.iter_game_moves", explode)
    _worker(engine).run_once(conn)
    assert engine.closed


# --- failure ---------------------------------------------------------------


def test_a_failing_job_is_settled_not_left_running(conn, cache_off, monkeypatch):
    _enqueue(conn)

    def explode(*args, **kwargs):
        raise RuntimeError("engine died")

    monkeypatch.setattr("server.worker.iter_game_moves", explode)
    result = _worker().run_once(conn)

    assert result["status"] == "failed"
    row = db.get_job(conn, "job-1")
    assert row["status"] == "failed"
    assert "engine died" in row["error"]
    assert row["finished_at"]


def test_a_job_with_an_unparsable_pgn_fails_cleanly(conn, cache_off):
    db.insert_job(
        conn, job_id="bad", game_key="k", username="u", user_color="white",
        pgn="this is not a pgn", depth=8, multipv=2,
    )
    result = _worker().run_once(conn)
    assert result["status"] == "failed"
    assert "ValueError" in db.get_job(conn, "bad")["error"]


# --- cancellation ----------------------------------------------------------


def test_cancel_before_any_ply_aborts_the_job(conn, cache_off):
    _enqueue(conn)
    jobq.request_cancel(conn, "job-1")

    # The job is cancelled, so the claim cannot even take it.
    assert _worker().run_once(conn) is None
    assert db.get_job(conn, "job-1")["status"] == "cancelled"


def test_cancel_mid_job_stops_without_finalising(conn, cache_off, monkeypatch):
    """The core B3 property: a cancel is noticed between plies, and no report is
    written for a job the user stopped."""
    _enqueue(conn)
    worker = _worker()

    real_should_cancel = should_cancel
    calls = {"n": 0}

    def cancel_after_two(connection, job_id):
        calls["n"] += 1
        if calls["n"] > 2:
            jobq.request_cancel(connection, job_id)
        return real_should_cancel(connection, job_id)

    monkeypatch.setattr("server.worker.should_cancel", cancel_after_two)
    result = worker.run_once(conn)

    assert result["status"] == "cancelled"
    assert db.get_job(conn, "job-1")["status"] == "cancelled"
    assert db.get_report(conn, "chesscom:abc", "white") is None
    # Two plies were recorded before the cancel was noticed, and they remain.
    assert len(db.list_moves(conn, "job-1")) == 2


def test_cancelled_job_keeps_its_partial_rows(conn, cache_off, monkeypatch):
    """A cancel is not a rollback: the plies already written are true, and C3 is
    specified to show them. Cancelling at a *different* ply than the other cancel
    test guards against the count being an accident of that test's timing."""
    _enqueue(conn)
    worker = _worker()

    real_should_cancel = should_cancel
    calls = {"n": 0}

    def cancel_after_four(connection, job_id):
        calls["n"] += 1
        if calls["n"] > 4:
            jobq.request_cancel(connection, job_id)
        return real_should_cancel(connection, job_id)

    monkeypatch.setattr("server.worker.should_cancel", cancel_after_four)
    result = worker.run_once(conn)

    assert result["status"] == "cancelled"
    assert len(db.list_moves(conn, "job-1")) == 4
    assert db.get_report(conn, "chesscom:abc", "white") is None


def test_should_cancel_reads_the_database(conn, cache_off):
    """The API and the worker are different processes; only the row can carry the
    signal."""
    _enqueue(conn)
    assert should_cancel(conn, "job-1") is False
    jobq.request_cancel(conn, "job-1")
    assert should_cancel(conn, "job-1") is True


def test_request_cancel_is_a_no_op_on_a_finished_job(conn, cache_off):
    _enqueue(conn)
    _worker().run_once(conn)
    assert jobq.request_cancel(conn, "job-1") is False
    assert db.get_job(conn, "job-1")["status"] == "done"


# --- the durable cache -----------------------------------------------------
# The *key logic* (depth rule, engine versioning, FEN normalisation) is B1's and
# is tested in tests/test_cache.py against the in-memory backend. What is tested
# here is the storage half: that the SQLite backend inherits that logic unchanged
# and round-trips real engine output through the table.


def _store(cache, board, *, version, depth, multipv=2):
    cache.store(
        fen=board.fen(),
        engine_version=version,
        depth=depth,
        multipv=multipv,
        lines=FakeEngine().analyse(board, depth, multipv),
        board=board,
    )


def test_sqlite_cache_round_trips_lines(conn):
    board = chess.Board()
    cache = SqlitePositionCache(conn)
    _store(cache, board, version="Fake Engine 1", depth=8)

    depth, restored = cache.lookup(
        fen=board.fen(), engine_version="Fake Engine 1", depth=8, multipv=2, board=board
    )
    assert depth == 8
    assert restored[0]["pv"][0]
    assert restored[0]["score"].relative.score() == 20


def test_sqlite_cache_honours_the_depth_rule(conn):
    """The inherited rule applies to the durable backend too: a depth-8 row must
    not answer a depth-20 request."""
    board = chess.Board()
    cache = SqlitePositionCache(conn)
    _store(cache, board, version="e", depth=8)

    assert cache.lookup(
        fen=board.fen(), engine_version="e", depth=20, multipv=2, board=board
    ) is None
    assert cache.lookup(
        fen=board.fen(), engine_version="e", depth=8, multipv=2, board=board
    ) is not None


def test_sqlite_cache_does_not_overwrite_a_deeper_row(conn):
    """A late shallow re-search must not downgrade the stored deep result."""
    board = chess.Board()
    cache = SqlitePositionCache(conn)
    _store(cache, board, version="e", depth=20)
    _store(cache, board, version="e", depth=8)

    rows = conn.execute("SELECT depth FROM position_eval ORDER BY depth").fetchall()
    assert [r["depth"] for r in rows] == [8, 20]
    assert cache.lookup(
        fen=board.fen(), engine_version="e", depth=12, multipv=2, board=board
    )[0] == 20


def test_sqlite_cache_is_keyed_on_the_real_engine(conn):
    board = chess.Board()
    cache = SqlitePositionCache(conn)
    _store(cache, board, version="Stockfish 16", depth=8)

    assert cache.lookup(
        fen=board.fen(), engine_version="Stockfish 19", depth=8, multipv=2, board=board
    ) is None


def test_lines_json_round_trip_preserves_mate(conn):
    """A mate score must survive storage, or a cached position loses its mate."""
    board = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    lines = [{"score": chess.engine.PovScore(chess.engine.Mate(-2), chess.BLACK), "pv": []}]
    payload = lines_to_json(lines, board)
    restored = lines_from_json(payload)
    assert restored[0]["score"].is_mate()
    assert restored[0]["score"].relative.mate() == -2


def test_the_durable_cache_saves_a_second_job_the_searches(conn, monkeypatch):
    """End to end: two jobs over the same game search the positions once."""
    monkeypatch.setenv("CACHE_DIR", "./data/position_cache")
    _enqueue(conn, "job-1", game_key="chesscom:abc")
    first = FakeEngine()
    _worker(first).run_once(conn)
    assert len(first.calls) == 7  # 6 plies -> 7 positions

    # A second job for the *same* game, different colour, from a fresh engine.
    db.insert_job(
        conn, job_id="job-2", game_key="chesscom:abc", username="BEAST-ASH",
        user_color="black", pgn=PGN, depth=8, multipv=2,
    )
    second = FakeEngine()
    _worker(second).run_once(conn)
    assert second.calls == [], "a warmed cache must serve the whole game"


def test_default_cache_follows_config(conn, monkeypatch):
    from analyzer.cache import NullPositionCache

    monkeypatch.delenv("CACHE_DIR", raising=False)
    assert isinstance(default_cache_for(conn), NullPositionCache)

    monkeypatch.setenv("CACHE_DIR", "./data/position_cache")
    assert isinstance(default_cache_for(conn), SqlitePositionCache)


def test_position_eval_rows_are_written_by_a_real_job(conn, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", "./data/position_cache")
    _enqueue(conn)
    _worker().run_once(conn)
    count = conn.execute("SELECT COUNT(*) FROM position_eval").fetchone()[0]
    assert count == 7


# --- the loop --------------------------------------------------------------


def test_run_drains_the_queue_and_stops(conn, cache_off, tmp_path, monkeypatch):
    path = str(tmp_path / "loop.db")
    seed = db.connect(path)
    db.init_db(seed)
    for i in range(3):
        _enqueue(seed, f"job-{i}", game_key=f"chesscom:{i}")
    seed.close()

    monkeypatch.setenv("CACHE_DIR", "")  # off
    worker = Worker(db_path=path, engine_factory=FakeEngine, sleep_seconds=0.0)
    assert worker.run(stop_after=10) == 3

    check = db.connect(path)
    try:
        statuses = [r["status"] for r in check.execute("SELECT status FROM jobs")]
        assert statuses == ["done", "done", "done"]
    finally:
        check.close()


def test_run_stops_early_when_stop_after_is_reached(conn, cache_off, tmp_path):
    path = str(tmp_path / "limit.db")
    seed = db.connect(path)
    db.init_db(seed)
    for i in range(5):
        _enqueue(seed, f"job-{i}", game_key=f"chesscom:{i}")
    seed.close()

    worker = Worker(db_path=path, engine_factory=FakeEngine, sleep_seconds=0.0)
    assert worker.run(stop_after=2) == 2
