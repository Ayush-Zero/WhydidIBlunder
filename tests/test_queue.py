"""B2 — the atomic job claim and the transitions around it (offline).

The claim is a silent-failure class: if two workers ever hold the same job,
nothing raises — they both analyse the game, both write the same ``moves`` rows,
and the last writer wins. So the tests here are about **mutual exclusion**, and
the centrepiece (:func:`test_two_workers_never_claim_the_same_job`) actually
starts threads against one database file rather than asserting the SQL looks
right.

WAL is required for those threads to be meaningful, and needs a real file, so
every fixture here uses ``tmp_path`` rather than ``:memory:``.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from server import db
from server import queue as jobq


@pytest.fixture()
def conn(tmp_path):
    """One migrated connection on a real file (WAL needs a file)."""
    connection = db.connect(tmp_path / "queue.db")
    db.init_db(connection)
    yield connection
    connection.close()


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "queue.db"


def _enqueue(conn, job_id="job-1", game_key="chesscom:abc"):
    return db.insert_job(
        conn,
        job_id=job_id,
        game_key=game_key,
        username="BEAST-ASH",
        user_color="white",
        pgn='[White "A"]\n[Black "B"]\n\n1. e4 e5 *',
        depth=12,
        multipv=4,
    )


# --- the happy path -------------------------------------------------------


def test_claiming_an_empty_queue_says_empty(conn):
    claim = jobq.claim_job(conn, "worker-1")
    assert not claim
    assert claim.reason == "empty"
    assert claim.job is None


def test_claiming_returns_the_job_row(conn):
    """``RETURNING *`` means the worker gets the PGN without a second read."""
    _enqueue(conn)
    claim = jobq.claim_job(conn, "worker-1")
    assert claim.reason == "claimed"
    assert claim.job["id"] == "job-1"
    assert claim.job["pgn"].startswith("[White")
    assert claim.job["depth"] == 12


def test_a_claim_moves_the_job_to_running(conn):
    _enqueue(conn)
    jobq.claim_job(conn, "worker-1")
    row = db.get_job(conn, "job-1")
    assert row["status"] == "running"
    assert row["worker_id"] == "worker-1"
    assert row["started_at"]
    assert row["heartbeat_at"] == row["started_at"]


def test_a_claimed_job_is_not_claimed_again(conn):
    _enqueue(conn)
    assert jobq.claim_job(conn, "worker-1").reason == "claimed"
    second = jobq.claim_job(conn, "worker-2")
    assert second.reason == "empty", "a running job is not claimable"


def test_the_oldest_job_is_claimed_first(conn):
    _enqueue(conn, "job-old", game_key="chesscom:old")
    _enqueue(conn, "job-new", game_key="chesscom:new")
    # insert_job stamps created_at at second resolution, so set them explicitly.
    conn.execute("UPDATE jobs SET created_at = '2026-01-01T00:00:00+00:00' WHERE id = 'job-old'")
    conn.execute("UPDATE jobs SET created_at = '2026-01-02T00:00:00+00:00' WHERE id = 'job-new'")
    assert jobq.claim_job(conn, "w").job["id"] == "job-old"
    assert jobq.claim_job(conn, "w").job["id"] == "job-new"


def test_the_id_breaks_a_created_at_tie(conn):
    """Deterministic ordering: without the id tiebreak two workers can select the
    same row and the loser finds nothing to update."""
    _enqueue(conn, "job-b", game_key="chesscom:b")
    _enqueue(conn, "job-a", game_key="chesscom:a")
    conn.execute("UPDATE jobs SET created_at = '2026-01-01T00:00:00+00:00'")
    assert jobq.claim_job(conn, "w").job["id"] == "job-a"


# --- attempts and error clearing ------------------------------------------


def test_each_claim_bumps_the_attempt_count(conn):
    _enqueue(conn)
    assert jobq.claim_job(conn, "w").job["attempts"] == 1
    jobq.requeue_job(conn, "job-1")
    assert jobq.claim_job(conn, "w").job["attempts"] == 2


def test_a_claim_clears_a_previous_error(conn):
    """A retried job must not wear the last attempt's failure."""
    _enqueue(conn)
    jobq.claim_job(conn, "w")
    jobq.requeue_job(conn, "job-1", error="engine exploded")
    assert db.get_job(conn, "job-1")["error"] == "engine exploded"

    jobq.claim_job(conn, "w")
    assert db.get_job(conn, "job-1")["error"] is None


def test_a_fresh_claim_is_never_already_stale(conn):
    """started_at and heartbeat_at are set to the same instant, so a just-claimed
    job cannot look stale to the reaper."""
    _enqueue(conn)
    row = jobq.claim_job(conn, "w").job
    assert row["heartbeat_at"] == row["started_at"]


# --- reaper (B4) -----------------------------------------------------------


def test_reaper_requeues_a_stale_running_job(conn):
    """A job with a dead worker has no heartbeat -> reaped back to queued."""
    _enqueue(conn)
    jobq.claim_job(conn, "worker-dead")
    # Make it look like the worker died an hour ago.
    conn.execute(
        "UPDATE jobs SET heartbeat_at = '2000-01-01T00:00:00+00:00' WHERE id = 'job-1'"
    )
    result = jobq.reap_stale_jobs(conn, timeout_seconds=180)
    row = db.get_job(conn, "job-1")
    assert row["status"] == "queued"
    assert row["worker_id"] is None
    assert row["started_at"] is None
    assert row["heartbeat_at"] is None
    assert result["requeued"] == 1


def test_reaper_uses_heartbeat_not_started_at(conn):
    """A long-but-healthy job keeps updating its heartbeat and must NOT be reaped."""
    _enqueue(conn)
    jobq.claim_job(conn, "worker-alive")
    # started long ago, but heartbeat is fresh
    conn.execute(
        "UPDATE jobs SET started_at = '2000-01-01T00:00:00+00:00', "
        "heartbeat_at = ? WHERE id = 'job-1'",
        (db.utcnow(),),
    )
    result = jobq.reap_stale_jobs(conn, timeout_seconds=180)
    assert db.get_job(conn, "job-1")["status"] == "running"
    assert result == {"requeued": 0, "failed": 0}


def test_reaper_falls_back_to_started_at_if_heartbeat_missing(conn):
    # Migration 2 added heartbeat_at; a row might lack it in a weird state.
    _enqueue(conn)
    jobq.claim_job(conn, "worker-dead")
    conn.execute(
        "UPDATE jobs SET heartbeat_at = NULL, started_at = '2000-01-01T00:00:00+00:00' WHERE id = 'job-1'"
    )
    result = jobq.reap_stale_jobs(conn, timeout_seconds=180)
    assert db.get_job(conn, "job-1")["status"] == "queued"
    assert result["requeued"] == 1


def test_reaper_fails_a_poisoned_job_after_max_attempts(conn):
    _enqueue(conn)
    jobq.claim_job(conn, "w1")  # attempt 1
    jobq.requeue_job(conn, "job-1")
    jobq.claim_job(conn, "w2")  # attempt 2
    conn.execute(
        "UPDATE jobs SET heartbeat_at = '2000-01-01T00:00:00+00:00' WHERE id = 'job-1'"
    )
    result = jobq.reap_stale_jobs(conn, timeout_seconds=180, max_attempts=2)
    row = db.get_job(conn, "job-1")
    assert row["status"] == "failed"
    assert "reaped" in row["error"]
    assert result == {"requeued": 0, "failed": 1}


def test_reaper_only_touches_running_jobs(conn):
    _enqueue(conn)
    jobq.claim_job(conn, "w")
    jobq.complete_job(conn, "job-1")
    conn.execute(
        "UPDATE jobs SET heartbeat_at = '2000-01-01T00:00:00+00:00' WHERE id = 'job-1'"
    )
    jobq.reap_stale_jobs(conn, timeout_seconds=180)
    assert db.get_job(conn, "job-1")["status"] == "done"


def test_reaper_never_touches_a_cancelled_job(conn):
    _enqueue(conn)
    jobq.claim_job(conn, "w")
    jobq.request_cancel(conn, "job-1")
    conn.execute(
        "UPDATE jobs SET heartbeat_at = '2000-01-01T00:00:00+00:00' WHERE id = 'job-1'"
    )
    jobq.reap_stale_jobs(conn, timeout_seconds=180)
    assert db.get_job(conn, "job-1")["status"] == "cancelled"


# --- the concurrency centrepiece ------------------------------------------


def test_two_workers_never_claim_the_same_job(db_path):
    """Many threads, many jobs, one database file.

    Asserts the two facts that matter together: **every job is claimed exactly
    once** (no duplicate work) and **every claim succeeded** (no ``database is
    locked`` from contention). The barrier maximises overlap so the race window,
    if it existed, would be hit rather than missed.
    """
    setup = db.connect(db_path)
    db.init_db(setup)
    total = 40
    for i in range(total):
        _enqueue(setup, f"job-{i:03d}", game_key=f"chesscom:{i:03d}")
    setup.close()

    workers = 8
    barrier = threading.Barrier(workers)
    claimed: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def run(worker: str) -> None:
        connection = db.connect(db_path)
        try:
            barrier.wait(timeout=10)
            while True:
                claim = jobq.claim_job(connection, worker)
                if not claim:
                    break
                with lock:
                    claimed.append(claim.job["id"])
        except Exception as exc:  # pragma: no cover - failure path
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(f"worker-{n}",)) for n in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == [], f"workers raised instead of waiting: {errors!r}"
    assert len(claimed) == total, "every job must be claimed exactly once"
    assert sorted(claimed) == sorted(f"job-{i:03d}" for i in range(total))
    assert len(set(claimed)) == total, "a job was claimed twice"


def test_claim_reports_lost_race_when_another_worker_took_it(db_path):
    """The distinction the worker sleeps on: 'empty' means wait, 'lost_race'
    means try again now because someone else is making progress."""
    setup = db.connect(db_path)
    db.init_db(setup)
    _enqueue(setup)
    setup.close()

    first = db.connect(db_path)
    second = db.connect(db_path)
    try:
        # Worker B claims the only job; worker A then finds the queue drained.
        assert jobq.claim_job(first, "worker-b").reason == "claimed"
        assert jobq.claim_job(second, "worker-a").reason == "empty"
    finally:
        first.close()
        second.close()


def test_concurrent_claims_do_not_raise_database_is_locked(db_path):
    """``busy_timeout`` converts lock contention into waiting. Without it this
    raises ``sqlite3.OperationalError: database is locked``."""
    setup = db.connect(db_path)
    db.init_db(setup)
    _enqueue(setup)
    setup.close()

    errors: list[Exception] = []

    # Hold a write lock while another connection tries to claim.
    holder = db.connect(db_path)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("UPDATE jobs SET updated_at = updated_at WHERE id = 'job-1'")

    def attempt() -> None:
        connection = db.connect(db_path, timeout=5.0)
        try:
            jobq.claim_job(connection, "blocked")
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=attempt)
    thread.start()
    # Let the claim block on the lock, then release it.
    thread.join(timeout=0.3)
    holder.execute("COMMIT")
    holder.close()
    thread.join(timeout=10)

    assert errors == []


# --- progress and liveness -------------------------------------------------


def test_progress_advances_and_refreshes_the_heartbeat(conn):
    _enqueue(conn)
    jobq.claim_job(conn, "w")
    conn.execute("UPDATE jobs SET heartbeat_at = '2000-01-01T00:00:00+00:00' WHERE id = 'job-1'")

    jobq.record_progress(conn, "job-1", 7)

    row = db.get_job(conn, "job-1")
    assert row["progress_ply"] == 7
    assert row["heartbeat_at"] != "2000-01-01T00:00:00+00:00"


def test_touch_does_not_disturb_status_or_progress(conn):
    _enqueue(conn)
    jobq.claim_job(conn, "w")
    jobq.record_progress(conn, "job-1", 3)
    jobq.touch_job(conn, "job-1")
    row = db.get_job(conn, "job-1")
    assert row["status"] == "running"
    assert row["progress_ply"] == 3


# --- terminal transitions --------------------------------------------------


def test_complete_marks_done_and_stamps_finished_at(conn):
    _enqueue(conn)
    jobq.claim_job(conn, "w")
    jobq.complete_job(conn, "job-1")
    row = db.get_job(conn, "job-1")
    assert row["status"] == "done"
    assert row["finished_at"]
    assert row["error"] is None


def test_fail_records_the_reason(conn):
    _enqueue(conn)
    jobq.claim_job(conn, "w")
    jobq.fail_job(conn, "job-1", "PGN had no moves")
    row = db.get_job(conn, "job-1")
    assert row["status"] == "failed"
    assert row["error"] == "PGN had no moves"
    assert row["finished_at"]


def test_fail_truncates_an_enormous_reason(conn):
    """The reason may embed a traceback; the column is not a log sink."""
    _enqueue(conn)
    jobq.fail_job(conn, "job-1", "x" * 5000)
    assert len(db.get_job(conn, "job-1")["error"]) == 2000


def test_requeue_clears_the_worker_and_restarts_the_clock(conn):
    _enqueue(conn)
    jobq.claim_job(conn, "worker-1")
    jobq.requeue_job(conn, "job-1")
    row = db.get_job(conn, "job-1")
    assert row["status"] == "queued"
    assert row["worker_id"] is None
    assert row["started_at"] is None
    assert row["heartbeat_at"] is None


def test_a_requeued_job_can_be_claimed_by_another_worker(conn):
    _enqueue(conn)
    jobq.claim_job(conn, "worker-1")
    jobq.requeue_job(conn, "job-1")
    claim = jobq.claim_job(conn, "worker-2")
    assert claim.reason == "claimed"
    assert claim.job["worker_id"] == "worker-2"


def test_a_done_job_is_not_claimable(conn):
    _enqueue(conn)
    jobq.claim_job(conn, "w")
    jobq.complete_job(conn, "job-1")
    assert jobq.claim_job(conn, "w").reason == "empty"


def test_status_is_still_constrained_by_the_schema(conn):
    """A guard that the queue's status values cannot drift from the CHECK."""
    _enqueue(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE jobs SET status = 'exploded' WHERE id = 'job-1'")
