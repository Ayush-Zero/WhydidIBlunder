"""D1 — the Phase 2 done-criterion: POST a game → poll → fetch the full report.

This is the only test that exercises *every* layer in one go: the HTTP surface
(C1/C2), the queue (B2), the worker (B3), the analyser (Phase 1) and the stored
report read (C5). Everything upstream of the engine is the recorded Chess.com
fixture, so the test never touches the network; the engine is a real Stockfish,
so the test is marked ``live`` and skipped unless opted in:

    $env:RUN_LIVE_TESTS='1'
    .\\.venv\\Scripts\\python.exe -m pytest -m live tests/test_e2e_service.py

It deliberately asserts **plumbing and contract**, not which moves are flagged —
classifications shift with the engine version and belong to the golden fixture
(``tests/test_golden_game.py``). The game is the four-ply September fixture, so
the run stays fast while still producing a real report from a real search.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from analyzer.report import validate_report
from server import chesscom, db
from server.app import create_app
from server.chesscom import ChessComClient, game_key
from server.worker import Worker

pytestmark = pytest.mark.live

FIXTURES = Path(__file__).parent / "fixtures" / "chesscom"
ARCHIVES_URL = f"{chesscom.API_BASE}/player/beast-ash/games/archives"
JUL = f"{chesscom.API_BASE}/player/beast-ash/games/2026/07"
AUG = f"{chesscom.API_BASE}/player/beast-ash/games/2026/08"
SEP = f"{chesscom.API_BASE}/player/beast-ash/games/2026/09"

#: Fixed by this test, not by .env: the job must carry the server's configured
#: depth/MultiPV, and the report's header must agree with them.
DEPTH = 12
MULTIPV = 2


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


ARCHIVES = _load("archives.json")
LIVE_MONTH = _load("month_live.json")
EDGE_MONTH = _load("month_edge_cases.json")

#: BEAST-ASH as white, a real four-ply game from the recorded fixture.
GAME = LIVE_MONTH["games"][1]
KEY = game_key(GAME)
PLIES = 4
WHITE_MOVES = 2


class Router:
    """Serves the recorded archives; an unlisted URL is a 404 (empty month)."""

    def __init__(self) -> None:
        self.routes = {
            ARCHIVES_URL: (200, ARCHIVES),
            JUL: (200, EDGE_MONTH),
            AUG: (200, {"games": []}),
            SEP: (200, LIVE_MONTH),
        }
        self.hits: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.hits.append(url)
        status, payload = self.routes.get(url, (404, {"code": 404}))
        return httpx.Response(status, json=payload)


@contextmanager
def _running_app(tmp_path):
    """A booted app over recorded Chess.com data and a throwaway database."""
    router = Router()
    client = ChessComClient(
        transport=httpx.MockTransport(router),
        cache_dir=tmp_path / "chesscom_cache",
        now=lambda: 1_000.0,
        sleep=lambda _seconds: None,
        pause=0.0,
    )
    db_path = tmp_path / "e2e.db"
    app = create_app(
        configure_logging_now=False,
        chesscom_client=client,
        db_path=str(db_path),
    )
    with TestClient(app) as test_client:
        yield test_client, router, db_path


def _job_rows(db_path) -> list:
    connection = db.connect(db_path)
    try:
        return connection.execute(
            "SELECT * FROM jobs ORDER BY created_at, id"
        ).fetchall()
    finally:
        connection.close()


def test_post_poll_report_is_one_working_analysis(tmp_path, monkeypatch):
    """The whole spine, from a public game key to a schema-valid stored report."""
    # Keep the live run quick while still exercising the real engine. These are
    # read at request time (the API stores them on the job) and again by the
    # worker, so the report header must show exactly these values.
    monkeypatch.setenv("ANALYSIS_DEPTH", str(DEPTH))
    monkeypatch.setenv("ANALYSIS_MULTIPV", str(MULTIPV))
    monkeypatch.setenv("STOCKFISH_THREADS", "1")  # determinism, as documented
    monkeypatch.delenv("CACHE_DIR", raising=False)  # a fresh DB gains nothing
    monkeypatch.delenv("CHESSCOM_CACHE_DIR", raising=False)

    with _running_app(tmp_path) as (client, router, db_path):
        # --- C2: request the analysis ------------------------------------
        queued = client.post(
            "/api/analyze", json={"username": "beast-ash", "game_key": KEY}
        )
        assert queued.status_code == 202, queued.text
        body = queued.json()
        job_id = body["job_id"]
        assert body["status"] == "queued"
        assert body["reused"] is False
        assert body["user_color"] == "white"
        assert uuid.UUID(job_id).version == 4

        # The upstream lookup happened exactly once, through the fixture.
        assert ARCHIVES_URL in router.hits and SEP in router.hits

        # --- B2/B3: drain exactly that job with a real Stockfish ---------
        worker = Worker(db_path=str(db_path), sleep_seconds=0.0, run_reaper=False)
        assert worker.run(stop_after=1) == 1

        row = _job_rows(db_path)[0]
        assert row["id"] == job_id
        assert row["status"] == "done"
        assert row["error"] is None
        assert row["finished_at"]
        assert row["depth"] == DEPTH
        assert row["multipv"] == MULTIPV
        assert row["pgn"] == GAME["pgn"]

        # --- C3: poll the finished job -----------------------------------
        polled = client.get(f"/api/jobs/{job_id}")
        assert polled.status_code == 200, polled.text
        progress = polled.json()
        assert progress["status"] == "done"
        assert progress["progress_ply"] == PLIES
        assert len(progress["moves"]) == PLIES
        assert [move["san"] for move in progress["moves"]] == [
            "e4",
            "d5",
            "exd5",
            "Qxd5",
        ]
        assert [move["side"] for move in progress["moves"]] == [
            "white",
            "black",
            "white",
            "black",
        ]
        assert [move["ply"] for move in progress["moves"]] == [1, 2, 3, 4]
        for move in progress["moves"]:
            assert move["classification"] in {"ok", "inaccuracy", "mistake", "blunder"}
            assert move["best_san"]
            assert isinstance(move["tags"], list)

        # --- C5: fetch the stored report ---------------------------------
        fetched = client.get(f"/api/games/{KEY}/report?user_color=white")
        assert fetched.status_code == 200, fetched.text
        assert fetched.headers["content-type"].startswith("application/json")
        report = fetched.json()

        assert validate_report(report) == []
        assert report["summary"]["user_color"] == "white"
        assert report["summary"]["moves_analysed"] == WHITE_MOVES
        assert report["game"]["white"] == "BEAST-ASH"
        assert report["game"]["black"] == "Gopinath_27"
        assert report["game"]["result"] == "1-0"
        assert report["engine"]["name"]
        assert report["engine"]["depth"] == DEPTH
        assert report["engine"]["multipv"] == MULTIPV
        assert len(report["moves"]) == PLIES

        # The report is served verbatim: what C5 returns is the stored JSON,
        # not a re-serialisation that can drift from the frozen schema.
        connection = db.connect(db_path)
        try:
            stored = db.get_report(connection, KEY, "white")
        finally:
            connection.close()
        assert stored is not None
        assert json.loads(stored["report_json"]) == report

        # --- C2 again: the stored report wins, no second job -------------
        again = client.post(
            "/api/analyze", json={"username": "beast-ash", "game_key": KEY}
        )
        assert again.status_code == 200, again.text
        assert again.json()["status"] == "done"
        assert again.json()["reused"] is True
        assert again.json()["job_id"] == job_id
        assert len(_job_rows(db_path)) == 1, "a finished report must not requeue"

        # No orphaned work: the queue is empty and nothing is left running.
        states = [row["status"] for row in _job_rows(db_path)]
        assert states == ["done"]
