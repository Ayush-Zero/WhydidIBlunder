"""C3 — ``GET /api/jobs/{id}``: status, progress, and partial results.

The endpoint is a read-only view over the queue. These tests pin what a caller
can rely on while a job is unfinished:

* a queued job is honest about having no moves yet;
* a running job returns the prefix the worker actually persisted, in ply order;
* a failed/cancelled job keeps that prefix (it is still useful);
* ``error`` is the worker's reason and is set only on failure;
* polling never mutates the job.

Offline and engine-free: jobs and moves are seeded straight into SQLite. The
Chess.com client is injected only because ``create_app`` owns one.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from server import db
from server.app import create_app
from server.chesscom import ChessComClient

PGN = "1. e4 e5 2. Nf3 Nc6 *"

#: The public move shape. FENs are storage detail and must not leak.
MOVE_FIELDS = {
    "ply",
    "move_number",
    "side",
    "san",
    "best_san",
    "pv_san",
    "eval_before_cp",
    "eval_after_cp",
    "eval_before_win_pct",
    "eval_after_win_pct",
    "mate_before",
    "mate_after",
    "played_rank",
    "drop_win_pct",
    "classification",
    "tags",
    "suppressed",
    "suppress_reason",
}


@contextmanager
def running_app(tmp_path):
    """A booted app with a throwaway database; C3 never calls Chess.com."""
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


def _move(ply: int) -> dict:
    """A complete ``move_to_dict``-shaped row for the two test plies."""
    return {
        "ply": ply,
        "move_number": (ply + 1) // 2,
        "side": "white" if ply % 2 else "black",
        "san": f"m{ply}",
        "best_san": f"best{ply}",
        "pv_san": ["e4", "e5"],
        "played_rank": 2,
        "eval_before_cp": 30,
        "eval_after_cp": 10,
        "eval_before_win_pct": 54.2,
        "eval_after_win_pct": 51.9,
        "mate_before": None,
        "mate_after": None,
        "drop_win_pct": 2.3,
        "classification": "ok",
        "tags": ["fork"],
        "suppressed": True,
        "suppress_reason": "best move",
    }


def _seed(
    db_path: Path,
    *,
    job_id: str = "job-1",
    status: str = "queued",
    progress: int = 0,
    error: str | None = None,
    plies: tuple[int, ...] = (),
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
            "UPDATE jobs SET status = ?, progress_ply = ?, error = ? WHERE id = ?",
            (status, progress, error, job_id),
        )
        for ply in plies:
            db.upsert_move(
                connection,
                job_id=job_id,
                move=_move(ply),
                fen_before=f"fen-{ply}-before",
                fen_after=f"fen-{ply}-after",
            )
    finally:
        connection.close()


def test_a_queued_job_has_no_moves(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = client.get("/api/jobs/job-1")

    assert response.status_code == 200
    assert response.json() == {
        "job_id": "job-1",
        "status": "queued",
        "game_key": "chesscom:game-1",
        "user_color": "white",
        "progress_ply": 0,
        "moves": [],
        "error": None,
    }


def test_a_running_job_returns_its_prefix_in_ply_order(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, status="running", progress=2, plies=(2, 1))
        response = client.get("/api/jobs/job-1")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["progress_ply"] == 2
    assert [move["ply"] for move in body["moves"]] == [1, 2]


def test_the_move_shape_matches_the_report_contract(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, status="running", progress=1, plies=(1,))
        move = client.get("/api/jobs/job-1").json()["moves"][0]

    assert set(move) == MOVE_FIELDS
    assert move["pv_san"] == ["e4", "e5"]
    assert move["tags"] == ["fork"]
    assert move["suppressed"] is True
    assert move["suppress_reason"] == "best move"


def test_a_done_job_returns_every_move(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, status="done", progress=2, plies=(1, 2))
        response = client.get("/api/jobs/job-1")

    body = response.json()
    assert body["status"] == "done"
    assert body["error"] is None
    assert len(body["moves"]) == 2


def test_a_failed_job_keeps_its_partial_moves_and_error(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(
            db_path,
            status="failed",
            progress=1,
            error="EngineTimeoutError: no response in 30s",
            plies=(1,),
        )
        response = client.get("/api/jobs/job-1")

    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "EngineTimeoutError: no response in 30s"
    assert [move["ply"] for move in body["moves"]] == [1]


def test_a_cancelled_job_keeps_its_partial_moves(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, status="cancelled", progress=1, plies=(1,))
        response = client.get("/api/jobs/job-1")

    body = response.json()
    assert body["status"] == "cancelled"
    assert body["error"] is None
    assert [move["ply"] for move in body["moves"]] == [1]


def test_progress_reflects_persisted_moves_when_the_counter_lags(tmp_path):
    """A crash can write a move before advancing ``progress_ply``.

    The response must describe the results it actually contains, so progress is
    the larger of the worker's checkpoint and the persisted prefix.
    """
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path, status="running", progress=0, plies=(1, 2))
        response = client.get("/api/jobs/job-1")

    assert response.json()["progress_ply"] == 2


def test_polling_before_the_schema_exists_is_503(tmp_path):
    """A fresh deployment must say "not ready", not leak a SQLite traceback."""
    with running_app(tmp_path) as (client, _):
        response = client.get("/api/jobs/job-1")

    assert response.status_code == 503
    assert response.json()["detail"] == "database is not ready"


def test_an_unknown_job_is_404(tmp_path):
    with running_app(tmp_path) as (client, db_path):
        _seed(db_path)
        response = client.get("/api/jobs/does-not-exist")

    assert response.status_code == 404
    assert "does-not-exist" in response.json()["detail"]


def test_an_over_long_job_id_is_rejected_before_the_database(tmp_path):
    with running_app(tmp_path) as (client, _):
        response = client.get("/api/jobs/" + "a" * 65)

    assert response.status_code == 422
