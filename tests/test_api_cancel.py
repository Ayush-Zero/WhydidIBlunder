"""C4 — ``POST /api/jobs/{id}/cancel``: cooperative cancellation.

The endpoint flips the status row; the worker notices between plies. These tests
pin the API half of that contract:

* a queued job is cancelled before any worker can claim it;
* a running job is marked cancelled immediately, not when the worker exits;
* partial moves survive a cancel;
* a terminal job is left alone, so a double-click or a late request is harmless;
* unknown ids are 404, invalid ids are 422, and a missing schema is 503.

Offline and engine-free: jobs are seeded straight into SQLite. No worker runs.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from server import db
from server.app import create_app
from server.chesscom import ChessComClient

PGN = "1. e4 e5 2. Nf3 Nc6 *"


@contextmanager
def running_app(tmp_path):
    """A booted app with a throwaway database; C4 never calls Chess.com."""
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
    job_id: str = "job-1",
    status: str = "queued",
    error: str | None = None,
) -> None:
    connection = db.connect(db_path)
    try:
        db.init_db(connection)
        db.insert_job(
            connection,
            job_id=job_id,
            game_key="chesscom:game-1",
            username="beast-ash",
            user_color="white",
            pgn=PGN,
            depth=12,
            multipv=4,
        )
        connection.execute(
            "UPDATE jobs SET status = ?, error = ? WHERE id = ?",
            (status, error, job_id),
        )
    finally:
        connection.close()


def _job(db_path: Path, job_id: str = "job-1") -> dict:
    connection = db.connect(db_path)
    try:
        return dict(db.get_job(connection, job_id))
    finally:
        connection.close()


def test_cancelling_a_queued_job_marks_it_cancelled(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = client.post("/api/jobs/job-1/cancel")
        row = _job(db_path)

    assert response.status_code == 200
    assert response.json() == {
        "job_id": "job-1",
        "status": "cancelled",
        "cancelled": True,
    }
    assert row["status"] == "cancelled"
    assert row["finished_at"], "the terminal transition is stamped immediately"


def test_cancelling_a_running_job_marks_it_cancelled_immediately(tmp_path):
    """The API does not wait for the worker to notice.

    The row is the signal; the worker checks it between plies. Waiting here
    would make the endpoint block for as long as the current engine search.
    """
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, status="running")
        response = client.post("/api/jobs/job-1/cancel")
        row = _job(db_path)

    assert response.status_code == 200
    assert response.json()["cancelled"] is True
    assert row["status"] == "cancelled"


def test_cancelling_twice_is_harmless(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, status="running")
        first = client.post("/api/jobs/job-1/cancel")
        second = client.post("/api/jobs/job-1/cancel")

    assert first.json()["cancelled"] is True
    assert second.status_code == 200
    assert second.json() == {
        "job_id": "job-1",
        "status": "cancelled",
        "cancelled": False,
    }


@pytest.mark.parametrize("status", ["done", "failed", "cancelled"])
def test_a_terminal_job_is_left_alone(tmp_path, status):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, status=status, error="boom" if status == "failed" else None)
        response = client.post("/api/jobs/job-1/cancel")
        row = _job(db_path)

    assert response.status_code == 200
    assert response.json() == {
        "job_id": "job-1",
        "status": status,
        "cancelled": False,
    }
    assert row["status"] == status
    if status == "failed":
        assert row["error"] == "boom", "a no-op must not clear the failure reason"


def test_a_cancel_keeps_the_partial_moves(tmp_path):
    """A cancel is not a rollback: already-written analysis stays useful."""
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, status="running")
        connection = db.connect(db_path)
        try:
            db.upsert_move(
                connection,
                job_id="job-1",
                move={
                    "ply": 1,
                    "move_number": 1,
                    "side": "white",
                    "san": "e4",
                    "best_san": "e4",
                    "pv_san": ["e4", "e5"],
                    "played_rank": 1,
                    "eval_before_cp": 30,
                    "eval_after_cp": 28,
                    "eval_before_win_pct": 54.2,
                    "eval_after_win_pct": 53.9,
                    "mate_before": None,
                    "mate_after": None,
                    "drop_win_pct": 0.3,
                    "classification": "ok",
                    "tags": [],
                    "suppressed": True,
                    "suppress_reason": "best move",
                },
                fen_before="before",
                fen_after="after",
            )
        finally:
            connection.close()

        client.post("/api/jobs/job-1/cancel")
        polled = client.get("/api/jobs/job-1")

    body = polled.json()
    assert body["status"] == "cancelled"
    assert [move["ply"] for move in body["moves"]] == [1]


def test_an_unknown_job_is_404(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = client.post("/api/jobs/does-not-exist/cancel")

    assert response.status_code == 404
    assert "does-not-exist" in response.json()["detail"]


def test_an_over_long_job_id_is_rejected_before_the_database(tmp_path):
    with running_app(tmp_path) as (client, _):
        response = client.post("/api/jobs/" + "a" * 65 + "/cancel")

    assert response.status_code == 422


def test_cancelling_before_the_schema_exists_is_503(tmp_path):
    with running_app(tmp_path) as (client, _):
        response = client.post("/api/jobs/job-1/cancel")

    assert response.status_code == 503
    assert response.json()["detail"] == "database is not ready"
