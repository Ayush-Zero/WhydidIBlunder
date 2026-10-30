"""Phase 3 A1 — ``GET /api/games/{game_key}/positions``.

The endpoint is a pure read of ``game_reports`` plus the replay helper.  These
tests pin the HTTP contract:

* the report's SAN sequence becomes positions in ply order;
* ``phase`` describes the position before each move;
* reports remain keyed by ``(game_key, user_color)``;
* URL-shaped keys still route;
* missing reports are 404, bad parameters 422, an uninitialised schema 503, and
  a corrupt stored report is a 409 rather than a partial board.

Offline and engine-free: reports are written straight into SQLite.
"""

from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from pathlib import Path

import chess
import httpx
from fastapi.testclient import TestClient

from server import db
from server.app import create_app
from server.chesscom import ChessComClient

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "immortal_game.report.json").read_text(
        encoding="utf-8"
    )
)


@contextmanager
def running_app(tmp_path):
    """A booted app with a throwaway database; this endpoint never calls Chess.com."""
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
) -> None:
    connection = db.connect(db_path)
    try:
        db.init_db(connection)
        db.save_report(
            connection,
            game_key=game_key,
            user_color=user_color,
            job_id=None,
            report=FIXTURE if report is None else report,
        )
    finally:
        connection.close()


def _get(client: TestClient, game_key: str = "chesscom:game-1", color: str = "white"):
    return client.get(
        f"/api/games/{game_key}/positions", params={"user_color": color}
    )


def test_positions_are_returned_for_every_ply(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = _get(client)

    assert response.status_code == 200
    body = response.json()
    assert body["game_key"] == "chesscom:game-1"
    assert body["user_color"] == "white"
    assert body["starting_fen"] == chess.STARTING_FEN
    assert len(body["positions"]) == len(FIXTURE["moves"])

    first = body["positions"][0]
    after_e4 = chess.Board()
    after_e4.push_san("e4")
    assert first == {
        "ply": 1,
        "fen_before": chess.STARTING_FEN,
        "fen_after": after_e4.fen(),
        "phase": "opening",
    }
    assert [position["ply"] for position in body["positions"]] == list(
        range(1, len(FIXTURE["moves"]) + 1)
    )


def test_phase_is_the_position_before_the_move(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        body = _get(client).json()

    assert body["positions"][19]["ply"] == 20
    assert body["positions"][19]["phase"] == "opening"
    assert body["positions"][20]["ply"] == 21
    assert body["positions"][20]["phase"] == "middlegame"


def test_the_two_colours_are_separate_reports(tmp_path):
    black_report = copy.deepcopy(FIXTURE)
    black_report["moves"] = black_report["moves"][:3]

    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, user_color="white")
        _seed(db_path, user_color="black", report=black_report)
        white = _get(client, color="white")
        black = _get(client, color="black")

    assert white.status_code == 200
    assert black.status_code == 200
    assert len(white.json()["positions"]) == len(FIXTURE["moves"])
    assert len(black.json()["positions"]) == 3


def test_the_other_colour_is_not_a_fallback(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, user_color="white")
        response = _get(client, color="black")

    assert response.status_code == 404
    assert "black" in response.json()["detail"]


def test_an_unknown_game_is_404(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = _get(client, game_key="chesscom:other")

    assert response.status_code == 404
    assert "chesscom:other" in response.json()["detail"]


def test_a_url_shaped_game_key_still_routes(tmp_path):
    key = "chesscom-url:https://www.chess.com/game/daily/123"
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, game_key=key)
        response = _get(client, game_key=key)

    assert response.status_code == 200
    assert response.json()["game_key"] == key


def test_user_color_is_required(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = client.get("/api/games/chesscom:game-1/positions")

    assert response.status_code == 422


def test_an_invalid_user_color_is_422(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = _get(client, color="green")

    assert response.status_code == 422


def test_a_missing_schema_is_503(tmp_path):
    with running_app(tmp_path) as (client, _):
        response = _get(client)

    assert response.status_code == 503
    assert response.json()["detail"] == "database is not ready"


def test_an_over_long_game_key_is_rejected(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = _get(client, game_key="a" * 2049)

    assert response.status_code == 422


def test_a_corrupt_stored_report_is_409(tmp_path):
    report = {"moves": [{"ply": 1, "side": "white", "san": "NotAMove"}]}
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, report=report)
        response = _get(client)

    assert response.status_code == 409
    assert "cannot be replayed" in response.json()["detail"]


def test_invalid_stored_json_is_409(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        connection = db.connect(db_path)
        try:
            connection.execute(
                "UPDATE game_reports SET report_json = ? WHERE game_key = ?",
                ("{not json", "chesscom:game-1"),
            )
            connection.commit()
        finally:
            connection.close()

        response = _get(client)

    assert response.status_code == 409
    assert response.json()["detail"] == "stored report is not valid JSON"


def test_an_empty_report_returns_an_empty_position_list(tmp_path):
    report = copy.deepcopy(FIXTURE)
    report["moves"] = []

    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, report=report)
        response = _get(client)

    assert response.status_code == 200
    assert response.json()["positions"] == []
