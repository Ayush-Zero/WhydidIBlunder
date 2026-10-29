"""C5 — ``GET /api/games/{game_key}/report``: the stored analysis report.

The endpoint is a pure read of ``game_reports``. These tests pin the parts that
are easy to get wrong:

* the body is the frozen Phase 1 report, served as JSON (not a JSON string);
* reports are keyed by ``(game_key, user_color)``, so the colours do not leak
  into each other and ``user_color`` is required;
* URL-shaped keys (which contain ``:`` and ``/``) still route;
* a report whose job row is gone is still useful;
* unknown pairs are 404, bad parameters 422, and a missing schema 503.

Offline and engine-free: reports are written straight into SQLite.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from server import db
from server.app import create_app
from server.chesscom import ChessComClient

PGN = "1. e4 e5 2. Nf3 Nc6 *"

REPORT = {
    "schema_version": 1,
    "game": {
        "url": "https://www.chess.com/game/live/1",
        "white": {"username": "beast-ash", "result": "win"},
        "black": {"username": "opponent", "result": "checkmated"},
        "result": "1-0",
    },
    "engine": {"name": "Stockfish 17", "depth": 12, "multipv": 4},
    "moves": [
        {
            "ply": 1,
            "move_number": 1,
            "side": "white",
            "san": "e4",
            "best_san": "e4",
            "pv_san": ["e4", "e5"],
            "eval_before_cp": 30,
            "eval_after_cp": 28,
            "eval_before_win_pct": 54.2,
            "eval_after_win_pct": 53.9,
            "mate_before": None,
            "mate_after": None,
            "played_rank": 1,
            "drop_win_pct": 0.3,
            "classification": "ok",
            "tags": [],
            "suppressed": True,
            "suppress_reason": "best move",
        }
    ],
    "summary": {
        "moves_analysed": 1,
        "accuracy": 87.9,
        "blunders": 0,
        "mistakes": 0,
        "inaccuracies": 0,
        "ok": 1,
    },
}


@contextmanager
def running_app(tmp_path):
    """A booted app with a throwaway database; C5 never calls Chess.com."""
    client = ChessComClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
        cache_dir=None,
        pause=0.0,
    )
    db_path = tmp_path / "api.db"
    app = create_app(
        configure_logging_now=False,
        chesscom_client=client,
        db_path=str(db_path),
    )
    with TestClient(app) as test_client:
        yield test_client, db_path


def _seed(
    db_path: Path,
    *,
    game_key: str = "chesscom:game-1",
    user_color: str = "white",
    report: dict | None = None,
    job_id: str | None = None,
) -> None:
    connection = db.connect(db_path)
    try:
        db.init_db(connection)
        db.save_report(
            connection,
            game_key=game_key,
            user_color=user_color,
            job_id=job_id,
            report=REPORT if report is None else report,
        )
    finally:
        connection.close()


def test_a_stored_report_is_served_verbatim(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = client.get(
            "/api/games/chesscom:game-1/report", params={"user_color": "white"}
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == REPORT


def test_the_two_colours_are_separate_reports(tmp_path):
    black_report = copy.deepcopy(REPORT)
    black_report["summary"]["accuracy"] = 42.0
    black_report["game"]["white"], black_report["game"]["black"] = (
        black_report["game"]["black"],
        black_report["game"]["white"],
    )

    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, user_color="white")
        _seed(db_path, user_color="black", report=black_report)
        white = client.get(
            "/api/games/chesscom:game-1/report", params={"user_color": "white"}
        )
        black = client.get(
            "/api/games/chesscom:game-1/report", params={"user_color": "black"}
        )

    assert white.json()["summary"]["accuracy"] == 87.9
    assert black.json()["summary"]["accuracy"] == 42.0


def test_the_other_colour_is_not_a_fallback(tmp_path):
    """A report for White must not satisfy a request for Black."""
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, user_color="white")
        response = client.get(
            "/api/games/chesscom:game-1/report", params={"user_color": "black"}
        )

    assert response.status_code == 404
    assert "black" in response.json()["detail"]


def test_an_unknown_game_is_404(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = client.get(
            "/api/games/chesscom:other/report", params={"user_color": "white"}
        )

    assert response.status_code == 404
    assert "chesscom:other" in response.json()["detail"]


def test_user_color_is_required(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = client.get("/api/games/chesscom:game-1/report")

    assert response.status_code == 422


def test_an_invalid_user_color_is_422(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = client.get(
            "/api/games/chesscom:game-1/report", params={"user_color": "green"}
        )

    assert response.status_code == 422


def test_a_url_shaped_game_key_still_routes(tmp_path):
    """A3 falls back to ``chesscom-url:https://...``; the slashes are part of the key."""
    key = "chesscom-url:https://www.chess.com/game/daily/123"
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, game_key=key)
        response = client.get(f"/api/games/{key}/report", params={"user_color": "white"})

    assert response.status_code == 200
    assert response.json() == REPORT


def test_a_report_that_outlived_its_job_is_still_served(tmp_path):
    """``game_reports.job_id`` is ``ON DELETE SET NULL``; the report remains useful."""
    with running_app(tmp_path) as (client, db_path):
        connection = db.connect(db_path)
        try:
            db.init_db(connection)
            db.insert_job(
                connection,
                job_id="job-1",
                game_key="chesscom:game-1",
                username="beast-ash",
                user_color="white",
                pgn=PGN,
                depth=12,
                multipv=4,
            )
            db.save_report(
                connection,
                game_key="chesscom:game-1",
                user_color="white",
                job_id="job-1",
                report=REPORT,
            )
            connection.execute("DELETE FROM jobs WHERE id = 'job-1'")
        finally:
            connection.close()

        response = client.get(
            "/api/games/chesscom:game-1/report", params={"user_color": "white"}
        )

    assert response.status_code == 200
    assert response.json() == REPORT


def test_a_missing_schema_is_503(tmp_path):
    with running_app(tmp_path) as (client, _):
        response = client.get(
            "/api/games/chesscom:game-1/report", params={"user_color": "white"}
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "database is not ready"


def test_an_over_long_game_key_is_rejected(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = client.get(
            "/api/games/" + "a" * 2049 + "/report", params={"user_color": "white"}
        )

    assert response.status_code == 422
