"""D1 — opt-in live-engine smoke test for the try-again endpoint.

This is plumbing only: a real Stockfish is launched by the API-process grader,
searches the candidate's resulting position at the **stored report's** depth, and
the HTTP contract comes back intact. It deliberately does not assert a
classification for any interesting position; those belong to the offline fake
engine tests, where the score is deterministic.

    $env:RUN_LIVE_TESTS='1'
    .\\.venv\\Scripts\\python.exe -m pytest -m live tests/test_live_try.py
"""

from __future__ import annotations

from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from analyzer.winpct import win_pct
from server import db
from server.app import create_app
from server.chesscom import ChessComClient

pytestmark = pytest.mark.live

KEY = "chesscom:live-try"


def _report() -> dict:
    return {
        "schema_version": 1,
        "engine": {"name": "Stockfish 19", "depth": 6, "multipv": 1},
        "game": {
            "white": "White",
            "black": "Black",
            "result": "*",
            "date": "",
            "event": "",
            "eco": "",
            "link": "",
            "user_color": "white",
        },
        "moves": [
            {
                "ply": 1,
                "move_number": 1,
                "side": "white",
                "san": "e4",
                "best_san": "e4",
                "pv_san": ["e4", "e5"],
                "eval_before_cp": 0,
                "eval_after_cp": 0,
                "eval_before_win_pct": win_pct(0),
                "eval_after_win_pct": win_pct(0),
                "mate_before": None,
                "mate_after": None,
                "played_rank": 1,
                "drop_win_pct": 0.0,
                "classification": "ok",
                "tags": [],
                "suppressed": True,
                "suppress_reason": "best move",
            }
        ],
        "summary": {
            "user_color": "white",
            "moves_analysed": 1,
            "accuracy": 100.0,
            "blunders": 0,
            "mistakes": 0,
            "inaccuracies": 0,
            "ok": 1,
        },
    }


@contextmanager
def _client(tmp_path):
    app = create_app(
        configure_logging_now=False,
        db_path=str(tmp_path / "live-try.db"),
        chesscom_client=ChessComClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
            cache_dir=None,
            pause=0.0,
        ),
    )
    with TestClient(app) as client:
        connection = db.connect(tmp_path / "live-try.db")
        try:
            db.init_db(connection)
            db.save_report(
                connection,
                game_key=KEY,
                user_color="white",
                job_id=None,
                report=_report(),
            )
        finally:
            connection.close()
        yield client


def test_real_stockfish_plumbs_through_the_try_endpoint(tmp_path):
    with _client(tmp_path) as client:
        response = client.post(
            f"/api/games/{KEY}/try",
            json={"user_color": "white", "ply": 1, "move_san": "e4"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["legal"] is True
    assert body["accepted"] is True
    assert body["classification"] == "ok"
    assert body["best_san"] == "e4"
    assert body["pv_san"] == ["e4", "e5"]
    assert isinstance(body["eval_after_cp"], int)
    assert body["threshold_win_pct"] > 0
