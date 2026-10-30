"""D3 — reaper end to end: a *killed* worker's job is requeued and completed.

B4's reaper has unit tests, but the state it is written for — a ``running`` row
whose worker no longer exists — is hard to fake convincingly in-process: a fake
stale timestamp proves the SQL, not that process death leaves the database in
that state. This test does the real thing:

1. a helper **process** claims a job and starts analysing it;
2. the process is killed with ``TerminateProcess`` (``Popen.kill``), exactly like
   a crash — no cleanup, no graceful shutdown;
3. the next worker's scheduled reaper sees a ``running`` job whose heartbeat is
   older than the timeout, requeues it, claims it again (``attempts == 2``) and
   completes it;
4. no job is left ``running`` and a report exists.

The helper uses a deliberately slow *fake* engine: this test is about process
death and the reaper, not Stockfish, so it is part of the offline suite. A crash
with a real engine cannot be cleaned up from inside the killed process, which is
why D2 (cancel) owns the engine-lifetime assertion.

Before the second worker starts, ``JOB_TIMEOUT_SECONDS`` is set to ``0`` so the
cut-off is "anything older than now". A production reaper uses the configured
timeout (180 s); waiting that long in a test would only re-test the clock.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import chess
import chess.engine
import chess.pgn

from server import db
from server.worker import Worker

REPO = Path(__file__).resolve().parent.parent
SAMPLE = REPO / "samples" / "immortal_game.pgn"
DB_NAME = "d3.db"
GAME_KEY = "chesscom:d3-dead-worker"
USER_COLOR = "white"
DEPTH = 8
MULTIPV = 2

PGN = SAMPLE.read_text(encoding="utf-8")
TOTAL_PLIES = len(list(chess.pgn.read_game(io.StringIO(PGN)).mainline_moves()))

#: The fake engine used by the child. It is slow on purpose so the parent can
#: observe at least one persisted ply before killing the process.
CHILD_SCRIPT = """
import sys, time
sys.path.insert(0, {repo!r})

import chess
import chess.engine

from server.worker import Worker

class SlowEngine:
    name = "Slow Fake Engine"

    def analyse(self, board, depth, multipv, timeout=None):
        time.sleep(0.2)
        score = chess.engine.PovScore(chess.engine.Cp(0), board.turn)
        move = next(iter(board.legal_moves))
        return [
            {{"score": score, "pv": [move], "depth": depth}}
            for _ in range(multipv)
        ]

    def close(self):
        pass

Worker(
    db_path={db_path!r},
    engine_factory=SlowEngine,
    sleep_seconds=0.0,
    run_reaper=False,
).run(stop_after=1)
"""


class FastEngine:
    """The finishing engine: legal, deterministic, instant. No sleeps."""

    name = "Fast Fake Engine"

    def analyse(self, board: chess.Board, depth: int, multipv: int, timeout=None) -> list:
        score = chess.engine.PovScore(chess.engine.Cp(0), board.turn)
        move = next(iter(board.legal_moves))
        return [{"score": score, "pv": [move], "depth": depth} for _ in range(multipv)]

    def close(self) -> None:
        pass


def _snapshot(db_path: Path, job_id: str):
    connection = db.connect(db_path)
    try:
        job = db.get_job(connection, job_id)
        return dict(job) if job is not None else None, len(db.list_moves(connection, job_id))
    finally:
        connection.close()


def _wait_until_running_with_a_ply(db_path: Path, job_id: str, deadline_seconds: float = 60.0):
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        job, moves = _snapshot(db_path, job_id)
        if job is not None and job["status"] == "running" and moves >= 1:
            return job, moves
        time.sleep(0.02)
    raise AssertionError("the helper process never started the job")


def _start_helper(tmp_path: Path, db_path: Path) -> subprocess.Popen:
    script = tmp_path / "d3_dead_worker.py"
    script.write_text(
        textwrap.dedent(CHILD_SCRIPT.format(repo=str(REPO), db_path=str(db_path))),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO)
    env["JOB_TIMEOUT_SECONDS"] = "180"  # the helper must not time itself out
    env["CACHE_DIR"] = ""  # no durable cache in this test
    env["CHESSCOM_CACHE_DIR"] = ""
    return subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_a_killed_worker_job_is_reaped_and_completed(tmp_path, monkeypatch):
    db_path = tmp_path / DB_NAME
    connection = db.connect(db_path)
    try:
        db.init_db(connection)
        db.insert_job(
            connection,
            job_id="d3-job",
            game_key=GAME_KEY,
            username="BEAST-ASH",
            user_color=USER_COLOR,
            pgn=PGN,
            depth=DEPTH,
            multipv=MULTIPV,
        )
    finally:
        connection.close()

    helper = _start_helper(tmp_path, db_path)
    try:
        running, moves_before_kill = _wait_until_running_with_a_ply(db_path, "d3-job")
        assert running["worker_id"], "a claimed job names its worker"
        assert running["attempts"] == 1

        # --- the crash: no cleanup, no shutdown hook --------------------
        helper.kill()
        helper.wait(timeout=30.0)
        assert helper.poll() is not None

        crashed, moves_after_kill = _snapshot(db_path, "d3-job")
        assert crashed["status"] == "running", "the row is left exactly as a crash leaves it"
        assert crashed["worker_id"] == running["worker_id"]
        assert moves_after_kill >= moves_before_kill
        assert 0 < moves_after_kill < TOTAL_PLIES, "the job really was interrupted"

        # --- the next worker's scheduled reaper takes over --------------
        monkeypatch.setenv("JOB_TIMEOUT_SECONDS", "0")  # any heartbeat is stale
        monkeypatch.setenv("REAPER_INTERVAL_SECONDS", "1")
        monkeypatch.setenv("CACHE_DIR", "")

        finished = Worker(
            db_path=str(db_path),
            engine_factory=FastEngine,
            sleep_seconds=0.0,
            run_reaper=True,
        ).run(stop_after=1)

        assert finished == 1, "the reaper should have requeued one job"
    finally:
        if helper.poll() is None:  # pragma: no cover - only if an assert failed early
            helper.kill()
            helper.wait(timeout=30.0)

    connection = db.connect(db_path)
    try:
        job = db.get_job(connection, "d3-job")
        moves = db.list_moves(connection, "d3-job")
        report = db.get_report(connection, GAME_KEY, USER_COLOR)
        running_jobs = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE status = 'running'"
        ).fetchone()[0]
    finally:
        connection.close()

    # The dead attempt was retired and the job completed on the second one.
    assert job["status"] == "done"
    assert job["attempts"] == 2
    assert job["worker_id"] != running["worker_id"]
    assert job["finished_at"]
    assert job["error"] is None, "the retry reason must be cleared on claim/done"
    assert running_jobs == 0, "no job may be left running forever"

    assert len(moves) == TOTAL_PLIES
    assert job["progress_ply"] == TOTAL_PLIES
    assert report is not None
    report_dict = json.loads(report["report_json"])
    assert report_dict["summary"]["moves_analysed"] > 0
    assert report["engine_version"] == "Fast Fake Engine"
