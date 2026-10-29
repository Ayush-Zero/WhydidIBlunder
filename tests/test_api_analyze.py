"""C2 — ``POST /api/analyze``: queueing, dedupe and refusal.

The endpoint's job is to turn one game from the list into one job. These tests
pin the three precedence rules (stored report → active job → new job), the
refusals that keep an unanalysable game out of the queue, and that the depth
comes from server config rather than the request.

Offline throughout: the Chess.com client is injected with a mock transport over
``tests/fixtures/chesscom``.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from server import chesscom, db, queue
from server.app import create_app
from server.chesscom import ChessComClient, game_key

FIXTURES = Path(__file__).parent / "fixtures" / "chesscom"
ARCHIVES_URL = f"{chesscom.API_BASE}/player/beast-ash/games/archives"
JUL = f"{chesscom.API_BASE}/player/beast-ash/games/2026/07"
AUG = f"{chesscom.API_BASE}/player/beast-ash/games/2026/08"
SEP = f"{chesscom.API_BASE}/player/beast-ash/games/2026/09"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


ARCHIVES = _load("archives.json")
LIVE_MONTH = _load("month_live.json")
EDGE_MONTH = _load("month_edge_cases.json")
LIVE_GAMES = LIVE_MONTH["games"]
EDGE_GAMES = {
    game["white"]["username"]: game for game in EDGE_MONTH["games"] if game.get("white")
}

#: The September game in which BEAST-ASH played white — the happy path.
WHITE_GAME = LIVE_GAMES[1]
WHITE_KEY = game_key(WHITE_GAME)


class Router:
    """Routes requests to recorded responses; anything unlisted is a 404."""

    def __init__(self, routes: dict[str, tuple[int, object]]):
        self.routes = routes
        self.hits: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.hits.append(url)
        if url in self.routes:
            status, payload = self.routes[url]
            return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"code": 404})


def standard_routes() -> dict[str, tuple[int, object]]:
    return {
        ARCHIVES_URL: (200, ARCHIVES),
        JUL: (200, EDGE_MONTH),
        AUG: (200, {"games": []}),
        SEP: (200, LIVE_MONTH),
    }


@contextmanager
def running_app(routes, tmp_path):
    """A booted app with an offline client and a throwaway database."""
    router = Router(routes)
    client = ChessComClient(
        transport=httpx.MockTransport(router),
        cache_dir=tmp_path / "chesscom_cache",
        now=lambda: 1_000.0,
        sleep=lambda _seconds: None,
        pause=0.0,
    )
    db_path = tmp_path / "api.db"
    app = create_app(
        configure_logging_now=False,
        chesscom_client=client,
        db_path=str(db_path),
    )
    with TestClient(app) as test_client:
        yield test_client, router, db_path


def _jobs(db_path):
    connection = db.connect(db_path)
    try:
        return connection.execute(
            "SELECT * FROM jobs ORDER BY created_at, id"
        ).fetchall()
    finally:
        connection.close()


def _report(job_id: str, *, depth: int = 12) -> dict:
    """The minimum ``save_report`` needs; C5 owns the full shape."""
    return {
        "schema_version": 1,
        "engine": {"name": "Stockfish 19", "depth": depth, "multipv": 4},
        "summary": {
            "moves_analysed": 10,
            "accuracy": 88.0,
            "blunders": 1,
            "mistakes": 2,
            "inaccuracies": 3,
            "ok": 4,
        },
    }


# --- the happy path -------------------------------------------------------


def test_analyze_queues_a_job_and_stores_the_pgn(tmp_path):
    with running_app(standard_routes(), tmp_path) as (client, _, db_path):
        response = client.post(
            "/api/analyze", json={"username": "beast-ash", "game_key": WHITE_KEY}
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["reused"] is False
    assert body["user_color"] == "white"
    assert body["game_key"] == WHITE_KEY
    assert body["job_id"]

    row = _jobs(db_path)[0]
    assert row["id"] == body["job_id"]
    assert row["game_key"] == WHITE_KEY
    assert row["username"] == "beast-ash"
    assert row["user_color"] == "white"
    assert row["status"] == "queued"
    assert row["pgn"] == WHITE_GAME["pgn"]
    meta = json.loads(row["game_meta"])
    assert meta["white"] == "BEAST-ASH"
    assert meta["black"] == "Gopinath_27"
    assert meta["result"] == "1-0"


def test_depth_and_multipv_come_from_config_not_the_request(tmp_path, monkeypatch):
    monkeypatch.setenv("ANALYSIS_DEPTH", "18")
    monkeypatch.setenv("ANALYSIS_MULTIPV", "3")

    with running_app(standard_routes(), tmp_path) as (client, _, db_path):
        response = client.post(
            "/api/analyze", json={"username": "beast-ash", "game_key": WHITE_KEY}
        )

    assert response.status_code == 202
    row = _jobs(db_path)[0]
    assert row["depth"] == 18
    assert row["multipv"] == 3


def test_analyze_accepts_a_bot_game(tmp_path):
    """Bots are included by default in C1, so they must be analysable here."""
    game = json.loads(json.dumps(WHITE_GAME))
    game["uuid"] = "bot-game-0000-0000-0000-000000000001"
    game["black"] = {
        "username": "sven--bot",
        "rating": None,
        "result": "checkmated",
        "user_title": "bot",
    }
    routes = standard_routes()
    routes[SEP] = (200, {"games": [game]})

    with running_app(routes, tmp_path) as (client, _, _):
        response = client.post(
            "/api/analyze",
            json={"username": "beast-ash", "game_key": game_key(game)},
        )

    assert response.status_code == 202
    assert response.json()["status"] == "queued"


# --- dedupe ---------------------------------------------------------------


def test_a_second_request_reuses_the_active_job(tmp_path):
    payload = {"username": "beast-ash", "game_key": WHITE_KEY}

    with running_app(standard_routes(), tmp_path) as (client, _, db_path):
        first = client.post("/api/analyze", json=payload)
        second = client.post("/api/analyze", json=payload)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["reused"] is True
    assert second.json()["status"] == "queued"
    assert len(_jobs(db_path)) == 1, "a double-click must not queue twice"


def test_a_stored_report_wins_over_a_new_job(tmp_path):
    payload = {"username": "beast-ash", "game_key": WHITE_KEY}

    with running_app(standard_routes(), tmp_path) as (client, _, db_path):
        connection = db.connect(db_path)
        try:
            db.init_db(connection)
            db.insert_job(
                connection,
                job_id="job-report",
                game_key=WHITE_KEY,
                username="beast-ash",
                user_color="white",
                pgn=WHITE_GAME["pgn"],
                depth=12,
                multipv=4,
            )
            db.save_report(
                connection,
                game_key=WHITE_KEY,
                user_color="white",
                job_id="job-report",
                report=_report("job-report"),
            )
            queue.complete_job(connection, "job-report")
        finally:
            connection.close()

        response = client.post("/api/analyze", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["reused"] is True
    assert body["job_id"] == "job-report"
    assert len(_jobs(db_path)) == 1, "no new job for a game that already has a report"


def test_a_stored_report_whose_job_is_gone_still_returns_done(tmp_path):
    """``game_reports.job_id`` is ON DELETE SET NULL; the report remains useful."""
    with running_app(standard_routes(), tmp_path) as (client, _, db_path):
        connection = db.connect(db_path)
        try:
            db.init_db(connection)
            db.save_report(
                connection,
                game_key=WHITE_KEY,
                user_color="white",
                job_id=None,
                report=_report("gone"),
            )
        finally:
            connection.close()

        response = client.post(
            "/api/analyze", json={"username": "beast-ash", "game_key": WHITE_KEY}
        )

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert response.json()["job_id"] is None


def test_a_finished_job_does_not_block_a_new_request(tmp_path):
    """A failed/cancelled/done job is outside the active-job index: retry works."""
    with running_app(standard_routes(), tmp_path) as (client, _, db_path):
        connection = db.connect(db_path)
        try:
            db.init_db(connection)
            db.insert_job(
                connection,
                job_id="old-failed",
                game_key=WHITE_KEY,
                username="beast-ash",
                user_color="white",
                pgn=WHITE_GAME["pgn"],
                depth=12,
                multipv=4,
            )
            queue.fail_job(connection, "old-failed", "engine died")
        finally:
            connection.close()

        response = client.post(
            "/api/analyze", json={"username": "beast-ash", "game_key": WHITE_KEY}
        )

    assert response.status_code == 202
    assert response.json()["reused"] is False
    assert response.json()["job_id"] != "old-failed"
    assert len(_jobs(db_path)) == 2


# --- refusals -------------------------------------------------------------


def test_unknown_player_is_404(tmp_path):
    with running_app({}, tmp_path) as (client, _, _):
        response = client.post(
            "/api/analyze", json={"username": "nobody-here", "game_key": WHITE_KEY}
        )

    assert response.status_code == 404
    assert "nobody-here" in response.json()["detail"]


def test_unknown_game_is_404(tmp_path):
    with running_app(standard_routes(), tmp_path) as (client, _, _):
        response = client.post(
            "/api/analyze",
            json={"username": "beast-ash", "game_key": "chesscom:not-a-real-game"},
        )

    assert response.status_code == 404
    assert "not-a-real-game" in response.json()["detail"]


def test_months_bounds_the_search(tmp_path):
    """A July game is out of range at one month and found at three."""
    key = game_key(EDGE_GAMES["NoPgn"])

    with running_app(standard_routes(), tmp_path) as (client, _, _):
        narrow = client.post(
            "/api/analyze",
            json={"username": "beast-ash", "game_key": key, "months": 1},
        )
        wide = client.post(
            "/api/analyze",
            json={"username": "beast-ash", "game_key": key, "months": 3},
        )

    assert narrow.status_code == 404
    assert wide.status_code == 422  # found, but it has no PGN


def test_from_position_games_are_refused(tmp_path):
    key = game_key(EDGE_GAMES["FromPosByType"])

    with running_app(standard_routes(), tmp_path) as (client, _, _):
        response = client.post(
            "/api/analyze", json={"username": "beast-ash", "game_key": key}
        )

    assert response.status_code == 422
    assert "custom position" in response.json()["detail"]


def test_a_game_without_a_pgn_is_refused(tmp_path):
    key = game_key(EDGE_GAMES["NoPgn"])

    with running_app(standard_routes(), tmp_path) as (client, _, _):
        response = client.post(
            "/api/analyze", json={"username": "beast-ash", "game_key": key}
        )

    assert response.status_code == 422
    assert "no PGN" in response.json()["detail"]


def test_a_username_that_did_not_play_the_game_is_refused(tmp_path):
    """July's fixture games are between other players; they are not yours to analyse."""
    key = game_key(EDGE_GAMES["NoUuidUrl"])

    with running_app(standard_routes(), tmp_path) as (client, _, _):
        response = client.post(
            "/api/analyze", json={"username": "beast-ash", "game_key": key}
        )

    assert response.status_code == 422
    assert "did not play" in response.json()["detail"]


def test_upstream_failure_is_502(tmp_path):
    with running_app({ARCHIVES_URL: (429, {"code": 429})}, tmp_path) as (client, _, _):
        response = client.post(
            "/api/analyze", json={"username": "beast-ash", "game_key": WHITE_KEY}
        )

    assert response.status_code == 502
    assert "chess.com" in response.json()["detail"].lower()


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "beast-ash"},  # no game_key
        {"username": "beast-ash", "game_key": ""},
        {"username": "bad name", "game_key": "chesscom:x"},
        {"username": "beast-ash", "game_key": "chesscom:x", "months": 0},
        # A typo in the field name must not be silently ignored.
        {"username": "beast-ash", "game_uuid": "chesscom:x"},
    ],
)
def test_invalid_bodies_are_rejected(tmp_path, payload):
    with running_app(standard_routes(), tmp_path) as (client, router, _):
        assert client.post("/api/analyze", json=payload).status_code == 422
    assert router.hits == [], "validation must happen before any upstream call"
