"""D2 — cancel end to end: a real job, stopped mid-flight, with no leaked engine.

The API half (C4) and the worker half (B3) are tested separately; what is untested
until here is the *seam*: a job claimed by a worker in one thread, cancelled by
the API from another, with a real Stockfish process in between. The properties
this pins:

* the cancel is noticed between plies, while the job is genuinely ``running``;
* the job settles as ``cancelled`` (not ``done``), with no report written;
* the plies already persisted survive and are what C3 reports;
* the worker's engine process has **exited** when the worker returns.

Live and opt-in, because it launches Stockfish:

    $env:RUN_LIVE_TESTS='1'
    .\\.venv\\Scripts\\python.exe -m pytest -m live tests/test_e2e_cancel.py

The recorded four-ply fixture is too short to reliably interrupt, so this test
wraps the bundled sample PGN in the same recorded-response shape. Upstream is
still a mock transport: no network is touched.
"""

from __future__ import annotations

import ctypes
import io
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import chess.pgn
import httpx
import pytest
from fastapi.testclient import TestClient

from analyzer import config as analyzer_config
from analyzer.engine import Engine
from server import chesscom, db, queue
from server.app import create_app
from server.chesscom import ChessComClient, game_key
from server.worker import Worker

pytestmark = pytest.mark.live

FIXTURES = Path(__file__).parent / "fixtures" / "chesscom"
SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "immortal_game.pgn"
ARCHIVES_URL = f"{chesscom.API_BASE}/player/beast-ash/games/archives"
JUL = f"{chesscom.API_BASE}/player/beast-ash/games/2026/07"
AUG = f"{chesscom.API_BASE}/player/beast-ash/games/2026/08"
SEP = f"{chesscom.API_BASE}/player/beast-ash/games/2026/09"

DEPTH = 8
MULTIPV = 2


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _long_game() -> dict:
    """A fixture-shaped game carrying a PGN long enough to interrupt mid-run."""
    game = json.loads(json.dumps(_load("month_live.json")["games"][1]))
    game["uuid"] = "d2-cancel-0000-0000-0000-000000000001"
    game.pop("url", None)
    game.pop("link", None)
    game["pgn"] = SAMPLE.read_text(encoding="utf-8")
    return game


GAME = _long_game()
KEY = game_key(GAME)
TOTAL_PLIES = len(
    list(chess.pgn.read_game(io.StringIO(GAME["pgn"])).mainline_moves())
)


class Router:
    """Recorded archives, with the long game served as September's only game."""

    def __init__(self) -> None:
        self.routes = {
            ARCHIVES_URL: (200, _load("archives.json")),
            JUL: (200, _load("month_edge_cases.json")),
            AUG: (200, {"games": []}),
            SEP: (200, {"games": [GAME]}),
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        status, payload = self.routes.get(str(request.url), (404, {"code": 404}))
        return httpx.Response(status, json=payload)


@contextmanager
def _running_app(tmp_path):
    client = ChessComClient(
        transport=httpx.MockTransport(Router()),
        cache_dir=tmp_path / "chesscom_cache",
        now=lambda: 1_000.0,
        sleep=lambda _seconds: None,
        pause=0.0,
    )
    db_path = tmp_path / "d2.db"
    app = create_app(
        configure_logging_now=False,
        chesscom_client=client,
        db_path=str(db_path),
    )
    with TestClient(app) as test_client:
        yield test_client, db_path


def _snapshot(db_path, job_id: str):
    connection = db.connect(db_path)
    try:
        job = db.get_job(connection, job_id)
        return dict(job) if job is not None else None, len(db.list_moves(connection, job_id))
    finally:
        connection.close()


def _wait_until_running_with_a_ply(db_path, job_id: str, deadline_seconds: float = 60.0):
    """Wait for the worker to claim the job and persist at least one real ply."""
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        job, moves = _snapshot(db_path, job_id)
        if job is not None and job["status"] == "running" and moves >= 1:
            return job, moves
        time.sleep(0.01)
    raise AssertionError("the worker never started the job, so nothing was cancelled")


def _process_alive(pid: int) -> bool:
    """Whether ``pid`` is still a live process (without a psutil dependency)."""
    if os.name == "nt":
        # os.kill(pid, 0) is not a probe on Windows: any signal but CTRL_* would
        # terminate the process. OpenProcess + STILL_ACTIVE is the safe check.
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # Unix: signal 0 only checks existence
    except ProcessLookupError:
        return False
    return True


def test_cancel_stops_a_real_job_and_leaks_no_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("ANALYSIS_DEPTH", str(DEPTH))
    monkeypatch.setenv("ANALYSIS_MULTIPV", str(MULTIPV))
    monkeypatch.setenv("STOCKFISH_THREADS", "1")
    monkeypatch.delenv("CACHE_DIR", raising=False)
    monkeypatch.delenv("CHESSCOM_CACHE_DIR", raising=False)

    assert TOTAL_PLIES > 20, "the interrupt test needs a game that runs for a while"

    engines: list[tuple[Engine, int]] = []

    def engine_factory() -> Engine:
        engine = Engine(
            analyzer_config.stockfish_path(),
            threads=1,
            hash_mb=128,
        )
        # The pid is captured while the process is unmistakably alive; after the
        # worker returns it must no longer exist.
        engines.append((engine, engine._engine.transport.get_pid()))
        return engine

    outcome: dict = {}

    with _running_app(tmp_path) as (client, db_path):
        def run_worker() -> None:
            try:
                outcome["result"] = Worker(
                    db_path=str(db_path),
                    engine_factory=engine_factory,
                    sleep_seconds=0.0,
                    run_reaper=False,
                ).run(stop_after=1)
            except BaseException as exc:  # noqa: BLE001 - surfaced by assertions
                outcome["error"] = exc

        queued = client.post(
            "/api/analyze", json={"username": "beast-ash", "game_key": KEY}
        )
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["job_id"]

        thread = threading.Thread(target=run_worker, name="d2-worker", daemon=True)
        thread.start()
        try:
            # --- the job is genuinely mid-flight -------------------------
            _, before_cancel = _wait_until_running_with_a_ply(db_path, job_id)

            # --- C4: the API cancels it while the worker is still working -
            response = client.post(f"/api/jobs/{job_id}/cancel")
            assert response.status_code == 200, response.text
            assert response.json() == {
                "job_id": job_id,
                "status": "cancelled",
                "cancelled": True,
            }

            # The worker must notice between plies, not run the game to the end.
            thread.join(timeout=60.0)
            assert not thread.is_alive(), "the worker ignored the cancel"
        finally:
            # Never leave a live analysis behind when an assertion above fails.
            connection = db.connect(db_path)
            try:
                db.init_db(connection)
                queue.request_cancel(connection, job_id)
            finally:
                connection.close()
            thread.join(timeout=60.0)
            if thread.is_alive():  # pragma: no cover - only on a hard hang
                pytest.fail("the worker thread did not exit")

        assert "error" not in outcome, outcome.get("error")
        assert outcome["result"] == 1

        # --- the job settled cancelled, not done ------------------------
        job, moves = _snapshot(db_path, job_id)
        assert job["status"] == "cancelled"
        assert job["finished_at"]
        assert job["error"] is None, "a cancel is not a failure"
        assert 0 < moves < TOTAL_PLIES, (moves, TOTAL_PLIES)
        assert moves >= before_cancel
        assert job["progress_ply"] == moves

        # Partial work is kept, and no report is invented for a stopped job.
        polled = client.get(f"/api/jobs/{job_id}").json()
        assert polled["status"] == "cancelled"
        assert len(polled["moves"]) == moves
        assert [move["ply"] for move in polled["moves"]] == list(range(1, moves + 1))

        connection = db.connect(db_path)
        try:
            assert db.get_report(connection, KEY, "white") is None
        finally:
            connection.close()

        # --- no orphaned Stockfish --------------------------------------
        assert len(engines) == 1, "the worker must open exactly one engine"
        engine, pid = engines[0]
        assert engine._engine.transport.is_closing(), "the engine transport stayed open"
        assert not _process_alive(pid), f"Stockfish pid {pid} outlived the worker"
