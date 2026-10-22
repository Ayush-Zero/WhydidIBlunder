"""The job queue: the atomic claim, plus the transitions around it.

This is the concurrency core of Phase 2, and it is mostly reasoning rather than
SQL. The one thing that must be true is that **two workers can never hold the
same job**, because the failure is silent: both would run a full analysis, write
the same ``moves`` rows twice, and the second writer would win with no error
anywhere.

The answer is to make the claim a *single statement* that both selects and
mutates (:data:`_CLAIM_SQL`). A ``SELECT``-then-``UPDATE`` has a window between
the two in which another worker can select the same row — and that window is not
theoretical, it is the normal case under load.

Two supporting facts make the single statement actually atomic:

* ``BEGIN IMMEDIATE`` takes the **write** lock before the statement runs, so the
  claim's inner ``SELECT`` cannot see a job another worker is mid-claim on. A
  deferred transaction (sqlite3's default) only upgrades to a write lock on the
  first write, which is exactly the race we are avoiding — hence
  ``isolation_level=None`` in :func:`server.db.connect` and explicit
  ``BEGIN IMMEDIATE`` here.
* ``PRAGMA busy_timeout`` (set in :func:`server.db.connect`) makes a second
  worker *wait* for the lock instead of failing with ``database is locked``.

**The heartbeat.** :func:`touch_job` / :func:`record_progress` refresh
``heartbeat_at`` as plies complete. The reaper (B4) keys off that rather than off
``started_at``, because a large analysis may legitimately run for a long time
while a dead worker does not — the two must not look alike. The column is added
by migration 2.
"""

from __future__ import annotations

import sqlite3

from server.db import utcnow

__all__ = [
    "Claim",
    "claim_job",
    "complete_job",
    "fail_job",
    "record_progress",
    "request_cancel",
    "requeue_job",
    "touch_job",
]

#: A job may be attempted this many times before it is considered poisonous.
#: Enforced by the reaper (B4); stated here so the retry policy has one home.
MAX_ATTEMPTS = 2


# --------------------------------------------------------------------------- #
# claiming
# --------------------------------------------------------------------------- #
class Claim:
    """The outcome of trying to claim a job.

    A small value object rather than ``Optional[Row]`` because the two failure
    modes are genuinely different: an empty queue means "sleep and try again",
    while a lost race means "try again immediately, someone else is working".
    Collapsing them into ``None`` would make the worker either spin needlessly or
    sleep when it did not need to.
    """

    __slots__ = ("job", "reason")

    def __init__(self, job: sqlite3.Row | None, reason: str) -> None:
        self.job = job
        self.reason = reason  # "claimed" | "empty" | "lost_race"

    def __bool__(self) -> bool:
        return self.job is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        if self.job is None:
            return f"Claim({self.reason})"
        return f"Claim(id={self.job['id']!r}, attempt={self.job['attempts']})"


#: One statement that picks the oldest queued job *and* takes it in the same
#: breath. Ordering by ``(created_at, id)`` makes the choice deterministic when
#: two jobs were created in the same second — and the id tiebreak is not
#: cosmetic: without it SQLite is free to return either row, so two workers can
#: both select the same one and the loser then finds nothing to update.
#:
#: ``error`` is cleared on claim so an attempt's failure does not linger on a
#: retried job, and ``started_at``/``heartbeat_at`` are (re)set to the same
#: instant, so a job that has just been claimed is never already stale.
_CLAIM_SQL = """
UPDATE jobs
   SET status       = 'running',
       worker_id    = :worker_id,
       started_at   = :now,
       heartbeat_at = :now,
       attempts     = attempts + 1,
       updated_at   = :now,
       error        = NULL
 WHERE id = (
       SELECT id FROM jobs
        WHERE status = 'queued'
        ORDER BY created_at, id
        LIMIT 1
 )
RETURNING *
"""


def claim_job(conn: sqlite3.Connection, worker_id: str) -> Claim:
    """Atomically claim the oldest queued job, or report why there was none.

    ``BEGIN IMMEDIATE`` takes the write lock *before* the statement runs, so the
    inner ``SELECT`` cannot see a job another worker is in the middle of taking.
    The lock is held for one statement and then released, so contenders block for
    microseconds rather than for the duration of an analysis.

    Returns a :class:`Claim`; ``claim.job`` is a full job row (``RETURNING *``)
    so the worker has the PGN and parameters without a second read.
    """
    now = utcnow()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(_CLAIM_SQL, {"worker_id": worker_id, "now": now}).fetchone()
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")

    if row is not None:
        return Claim(row, "claimed")

    # Nothing was claimed. Distinguish "queue empty" from "raced and lost" — the
    # worker should sleep in the first case and retry at once in the second.
    waiting = conn.execute("SELECT 1 FROM jobs WHERE status = 'queued' LIMIT 1").fetchone()
    return Claim(None, "lost_race" if waiting else "empty")


# --------------------------------------------------------------------------- #
# progress and liveness
# --------------------------------------------------------------------------- #
def touch_job(conn: sqlite3.Connection, job_id: str) -> None:
    """Record a heartbeat without changing anything else.

    Called between plies. The reaper keys off this, so a long-but-healthy
    analysis is not mistaken for a dead one.
    """
    now = utcnow()
    conn.execute(
        "UPDATE jobs SET heartbeat_at = ?, updated_at = ? WHERE id = ?",
        (now, now, job_id),
    )


def record_progress(conn: sqlite3.Connection, job_id: str, plies_done: int) -> None:
    """Advance ``progress_ply`` and refresh the heartbeat in one statement.

    One statement on purpose: progress advancing and the job still being alive
    are the same fact, so they should never be observable apart.
    """
    now = utcnow()
    conn.execute(
        """
        UPDATE jobs
           SET progress_ply = ?, heartbeat_at = ?, updated_at = ?
         WHERE id = ?
        """,
        (int(plies_done), now, now, job_id),
    )


# --------------------------------------------------------------------------- #
# terminal-ish transitions
# --------------------------------------------------------------------------- #
def complete_job(conn: sqlite3.Connection, job_id: str) -> None:
    """Mark a job ``done`` and stamp ``finished_at``."""
    now = utcnow()
    conn.execute(
        """
        UPDATE jobs
           SET status = 'done', finished_at = ?, updated_at = ?, error = NULL
         WHERE id = ?
        """,
        (now, now, job_id),
    )


def fail_job(conn: sqlite3.Connection, job_id: str, error: str) -> None:
    """Mark a job ``failed`` with a reason.

    The reason is stored, not just logged: a failed job that does not say why is
    indistinguishable from a crashed worker, and the first thing anyone asks is
    "what went wrong". Truncated because the reason may embed a traceback.
    """
    now = utcnow()
    conn.execute(
        """
        UPDATE jobs
           SET status = 'failed', finished_at = ?, updated_at = ?, error = ?
         WHERE id = ?
        """,
        (now, now, str(error)[:2000], job_id),
    )


def requeue_job(conn: sqlite3.Connection, job_id: str, error: str | None = None) -> None:
    """Return a job to ``queued``, clearing the worker that had it.

    ``worker_id``/``started_at``/``heartbeat_at`` are cleared rather than kept:
    the next claim sets its own, and a stale worker id on a queued job would make
    it look like someone still owns it.
    """
    now = utcnow()
    conn.execute(
        """
        UPDATE jobs
           SET status = 'queued', worker_id = NULL, started_at = NULL,
               heartbeat_at = NULL, updated_at = ?, error = ?
         WHERE id = ?
        """,
        (now, error, job_id),
    )


def request_cancel(conn: sqlite3.Connection, job_id: str) -> bool:
    """Ask a job to stop. Returns whether the request changed anything.

    Only a ``queued`` or ``running`` job can be cancelled. The transition to
    ``cancelled`` is written immediately — even for a ``running`` job — because
    the worker polls this column between plies (:func:`server.worker.should_cancel`);
    there is no in-process channel from the API to the worker, so the database is
    the signal. A job that was already ``done``/``failed``/``cancelled`` is left
    alone and reported as "nothing to do", so a double-click is harmless.
    """
    now = utcnow()
    cursor = conn.execute(
        """
        UPDATE jobs
           SET status = 'cancelled', finished_at = ?, updated_at = ?
         WHERE id = ? AND status IN ('queued', 'running')
        """,
        (now, now, job_id),
    )
    return cursor.rowcount > 0
