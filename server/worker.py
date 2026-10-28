"""The worker loop: claim a job, analyse it ply by ply, persist, finalise.

This is the part that turns the queue (B2) and the analyser (Phase 1) into a
running service. Its job is not to be clever but to be **honest about
interruption**: at any moment it may be killed, and what it has already written
has to be a truthful partial result rather than a half-report that looks whole.

Three decisions shape the whole file.

**1. Plies are written as they happen, and progress with them.**
``iter_game_moves`` yields a ply at a time, so the loop persists each ``moves``
row and calls :func:`server.queue.record_progress` in the same breath. That is
what makes a poll response (C3) able to show real partial results for free, and
what makes the heartbeat meaningful — progress and liveness are recorded together,
because they are the same fact.

**2. Cancellation is cooperative, and checked *between* plies.**
:func:`should_cancel` is consulted before each ply; if the job is ``cancelled``
the loop stops, leaving the plies already written in place. There is deliberately
no attempt to kill Stockfish mid-search from here — that is the hard timeout (B5),
a different mechanism for a different problem. A cancel is a request, not a
guarantee of immediacy, and the API documents it as "within one ply".

**3. A job that finishes is only *done* once its report is stored.**
The order is: analyse all plies → build the report → ``save_report`` → mark the
job ``done``. If the process dies between those steps the job is still ``running``
and the reaper (B4) will requeue it, which is correct: a ``done`` job with no
report would be the one unrecoverable state, so it is the one ordering the code
never produces.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid

from analyzer.cache import NullPositionCache
from analyzer.pipeline import MoveReport, iter_game_moves, read_game
from analyzer.report import build_report, move_to_dict
from server import db
from server.position_cache import SqlitePositionCache
from server.queue import claim_job, complete_job, fail_job, reap_stale_jobs, record_progress

__all__ = ["Worker", "JobCancelled", "default_cache_for", "should_cancel"]

log = logging.getLogger("whydidiblunder.worker")


class JobCancelled(Exception):
    """Raised internally when a job's ``cancel_requested`` flag is set.

    A dedicated exception rather than returning early, so the cancellation path
    is *not* the success path: the loop cannot fall through into finalising a
    report for a job the user asked to stop.
    """


# --------------------------------------------------------------------------- #
# cancellation
# --------------------------------------------------------------------------- #
def should_cancel(conn: sqlite3.Connection, job_id: str) -> bool:
    """Has this job been asked to stop?

    Reads ``status`` back from the database rather than holding any in-process
    flag: the cancel endpoint (C4) runs in the *API* process, so the only channel
    between it and the worker is the database. A boolean in the worker's memory
    would never see it.
    """
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row is not None and row["status"] == "cancelled"


# --------------------------------------------------------------------------- #
# caching
# --------------------------------------------------------------------------- #
def default_cache_for(conn: sqlite3.Connection):
    """The durable position cache, unless it is switched off.

    ``CACHE_DIR`` unset means "no caching" (B1's deliberate default), represented
    here as :class:`~analyzer.cache.NullPositionCache` — a real value, so the
    pipeline's ``cache is None`` branch is reserved for "the caller passed
    nothing" and never doubles as "the operator disabled it".
    """
    from analyzer.config import cache_dir

    if cache_dir() is None:
        return NullPositionCache()
    return SqlitePositionCache(conn)


# --------------------------------------------------------------------------- #
# the worker
# --------------------------------------------------------------------------- #
class Worker:
    """One worker. Construct it, call :meth:`run_once` in a loop.

    ``worker_id`` is a uuid by default, so logs from two workers on one machine
    are distinguishable without any coordination. ``sleep_seconds`` is how long
    the loop waits when the queue is empty — long enough not to spin, short enough
    that a new job starts promptly.
    """

    def __init__(
        self,
        *,
        db_path: str | None = None,
        worker_id: str | None = None,
        engine_factory=None,
        sleep_seconds: float = 1.0,
        heartbeat_every: int = 1,
        run_reaper: bool = True,
    ) -> None:
        self.db_path = db_path
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        #: ``engine`` is opened per job (B1: settings come from config at that
        #: moment) and closed in a ``finally``, so a crash mid-analysis cannot
        #: leak a Stockfish process.
        self._engine_factory = engine_factory
        self.sleep_seconds = sleep_seconds
        #: How often to write a heartbeat, in plies. 1 means every ply; larger
        #: values reduce writes on long games at the cost of coarser liveness.
        self.heartbeat_every = max(1, int(heartbeat_every))
        #: The reaper runs inside this worker process for simplicity in dev/tests.
        #: Disable in tests where timing must be controlled explicitly.
        self.run_reaper = run_reaper

    # -- engine ---------------------------------------------------------
    def _open_engine(self):
        if self._engine_factory is not None:
            return self._engine_factory()
        from analyzer.config import (
            stockfish_hash_mb,
            stockfish_path,
            stockfish_threads,
        )
        from analyzer.engine import Engine

        return Engine(
            stockfish_path(),
            threads=stockfish_threads(),
            hash_mb=stockfish_hash_mb(),
        )

    # -- one job --------------------------------------------------------
    def process_job(self, conn: sqlite3.Connection, job: sqlite3.Row) -> dict:
        """Run one claimed job to completion. Returns a small result summary.

        Raises :class:`JobCancelled` if the job was cancelled; the caller
        (:meth:`run_once`) turns that into the ``cancelled`` status. Anything
        else propagates to be recorded as a failure.
        """
        job_id = job["id"]
        color = job["user_color"]
        depth = int(job["depth"])
        multipv = int(job["multipv"])

        game = read_game(job["pgn"])
        # ``read_game`` returns an *empty* game for text that is not a PGN at all
        # (python-chess treats it as a game with no moves), so "parsed" is not the
        # same as "usable". Without this guard such a job would store an empty
        # report and be marked ``done`` — a lie in the database, and the poll
        # endpoint would show a finished analysis of nothing.
        if not list(game.mainline_moves()):
            raise ValueError("the stored PGN contains no moves")
        cache = default_cache_for(conn)

        # Board snapshots up front: the FEN columns and the report's FENs both
        # come from here, and pushing a move is cheaper than re-parsing.
        board = game.board()
        boards = [board.copy()]
        for mv in game.mainline_moves():
            board.push(mv)
            boards.append(board.copy())
        total = len(boards) - 1

        engine = self._open_engine()
        try:
            engine_name = getattr(engine, "name", "unknown")
            reports: list[MoveReport] = []

            for index, _, report in iter_game_moves(
                game, engine=engine, depth=depth, multipv=multipv, cache=cache
            ):
                # Cooperative cancel, checked *before* the ply is recorded: a
                # cancelled job must not gain a row for work it is abandoning.
                if should_cancel(conn, job_id):
                    raise JobCancelled(job_id)

                wire = move_to_dict(report)
                db.upsert_move(
                    conn,
                    job_id=job_id,
                    move=wire,
                    fen_before=boards[index].fen(),
                    fen_after=boards[index + 1].fen(),
                )
                reports.append(report)

                if (index + 1) % self.heartbeat_every == 0:
                    # Progress and liveness in one write: "ply N is done" and
                    # "this worker is alive" are the same fact (see server.queue).
                    record_progress(conn, job_id, index + 1)

            # A late cancel still wins: otherwise a job could be marked done
            # moments after the user asked it to stop.
            if should_cancel(conn, job_id):
                raise JobCancelled(job_id)

            report_dict = build_report(game, reports, color, engine_name, depth, multipv)

            # Report first, status second. A ``done`` job with no report would be
            # unrecoverable; a ``running`` job with a report is simply re-run by
            # the reaper (B4) and overwrites it. So this ordering is the only one
            # whose failure mode is recoverable.
            db.save_report(
                conn,
                game_key=job["game_key"],
                user_color=color,
                job_id=job_id,
                report=report_dict,
            )
            complete_job(conn, job_id)
            return {
                "job_id": job_id,
                "plies": total,
                "status": "done",
                "accuracy": report_dict["summary"]["accuracy"],
            }
        finally:
            close = getattr(engine, "close", None)
            if callable(close):
                close()

    # -- the loop -------------------------------------------------------
    def run_once(self, conn: sqlite3.Connection) -> dict | None:
        """Claim and run at most one job.

        Returns a result dict, or ``None`` when there was nothing to do. The
        caller loops on this; tests call it directly, which is what makes the
        whole worker testable without threads or a sleeping process.

        Failure handling lives here rather than in :meth:`process_job` so the
        loop's contract is one sentence: *a claimed job always leaves ``running``*
        — it becomes ``done``, ``cancelled``, or ``failed``.
        """
        claim = claim_job(conn, self.worker_id)
        if not claim:
            return None

        job = claim.job
        job_id = job["id"]
        try:
            result = self.process_job(conn, job)
        except JobCancelled:
            # Leave the plies already written in place: a cancelled job's partial
            # results are still true, and C3 is specified to show them.
            conn.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ?, updated_at = ?"
                " WHERE id = ?",
                (db.utcnow(), db.utcnow(), job_id),
            )
            log.info("job cancelled", extra={"job_id": job_id})
            return {"job_id": job_id, "status": "cancelled"}
        except Exception as exc:  # noqa: BLE001 - any failure must leave the job settled
            fail_job(conn, job_id, f"{type(exc).__name__}: {exc}")
            log.exception("job failed", extra={"job_id": job_id})
            return {"job_id": job_id, "status": "failed"}

        log.info(
            "job done",
            extra={"job_id": job_id, "plies": result["plies"]},
        )
        return result

    def run(self, *, stop_after: int | None = None) -> int:
        """Loop :meth:`run_once` forever (until ``stop_after`` jobs are run).

        ``stop_after`` exists so a test or a script can drain the queue and exit
        instead of hanging; production passes nothing.

        Sleeps only when the queue was empty. A ``lost_race`` claim (B2) means
        another worker is making progress, so retrying immediately is right and
        sleeping would waste the moment.

        The reaper (B4) runs periodically inside the worker process to recover
        jobs left behind by a dead worker; it keys off ``heartbeat_at``.
        """
        from analyzer import config

        conn = db.connect(self.db_path)
        db.init_db(conn)
        done = 0
        last_reap = 0.0
        reaper_interval = config.reaper_interval_seconds()
        timeout = config.job_timeout_seconds()
        try:
            while stop_after is None or done < stop_after:
                now = time.monotonic()
                if self.run_reaper and reaper_interval > 0 and now - last_reap >= reaper_interval:
                    try:
                        report = reap_stale_jobs(conn, timeout_seconds=timeout)
                        if report["requeued"] or report["failed"]:
                            log.info(
                                "reaper swept stale jobs",
                                extra=report,
                            )
                    except Exception:  # noqa: BLE001 - reaper must not kill the worker
                        log.exception("reaper failed")
                    finally:
                        last_reap = now

                result = self.run_once(conn)
                if result is None:
                    if stop_after is not None:
                        break
                    time.sleep(self.sleep_seconds)
                    continue
                done += 1
        finally:
            conn.close()
        return done
