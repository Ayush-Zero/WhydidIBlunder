"""The worker loop: claim a job, analyse it ply by ply, persist, finalise.

This is the part that turns the queue (B2) and the analyser (Phase 1) into a
running service. Its job is not to be clever but to be **honest about
interruption**: at any moment it may be killed, and what it has already written
has to be a truthful partial result rather than a half-report that looks whole.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid

from analyzer.cache import NullPositionCache
from analyzer.engine import Engine, EngineTimeoutError
from analyzer.pipeline import MoveReport, iter_game_moves, read_game
from analyzer.report import build_report, move_to_dict
from server import db
from server.position_cache import SqlitePositionCache
from server.queue import claim_job, complete_job, fail_job, reap_stale_jobs, record_progress

__all__ = ["Worker", "JobCancelled", "default_cache_for", "should_cancel"]

log = logging.getLogger("whydidiblunder.worker")


class JobCancelled(Exception):
    """Raised internally when a job's ``cancel_requested`` flag is set."""


def should_cancel(conn: sqlite3.Connection, job_id: str) -> bool:
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return row is not None and row["status"] == "cancelled"


def default_cache_for(conn: sqlite3.Connection):
    from analyzer.config import cache_dir

    if cache_dir() is None:
        return NullPositionCache()
    return SqlitePositionCache(conn)


class Worker:
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
        self._engine_factory = engine_factory
        self.sleep_seconds = sleep_seconds
        self.heartbeat_every = max(1, int(heartbeat_every))
        self.run_reaper = run_reaper

    def _open_engine(self):
        if self._engine_factory is not None:
            return self._engine_factory()
        from analyzer.config import stockfish_hash_mb, stockfish_path, stockfish_threads

        return Engine(
            stockfish_path(),
            threads=stockfish_threads(),
            hash_mb=stockfish_hash_mb(),
        )

    def process_job(self, conn: sqlite3.Connection, job: sqlite3.Row) -> dict:
        job_id = job["id"]
        color = job["user_color"]
        depth = int(job["depth"])
        multipv = int(job["multipv"])

        game = read_game(job["pgn"])
        if not list(game.mainline_moves()):
            raise ValueError("the stored PGN contains no moves")
        cache = default_cache_for(conn)

        board = game.board()
        boards = [board.copy()]
        for mv in game.mainline_moves():
            board.push(mv)
            boards.append(board.copy())
        total = len(boards) - 1

        from analyzer import config

        per_move_timeout = float(config.job_timeout_seconds())
        engine = self._open_engine()
        try:
            engine_name = getattr(engine, "name", "unknown")
            reports: list[MoveReport] = []

            for index, _, report in iter_game_moves(
                game, engine=engine, depth=depth, multipv=multipv, cache=cache, per_move_timeout=per_move_timeout
            ):
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
                    record_progress(conn, job_id, index + 1)
            if should_cancel(conn, job_id):
                raise JobCancelled(job_id)

            report_dict = build_report(game, reports, color, engine_name, depth, multipv)

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

    def run_once(self, conn: sqlite3.Connection) -> dict | None:
        claim = claim_job(conn, self.worker_id)
        if not claim:
            return None

        job = claim.job
        job_id = job["id"]
        try:
            result = self.process_job(conn, job)
        except JobCancelled:
            conn.execute(
                "UPDATE jobs SET status = 'cancelled', finished_at = ?, updated_at = ? WHERE id = ?",
                (db.utcnow(), db.utcnow(), job_id),
            )
            log.info("job cancelled", extra={"job_id": job_id})
            return {"job_id": job_id, "status": "cancelled"}
        except EngineTimeoutError as exc:
            fail_job(conn, job_id, f"EngineTimeoutError: {exc}")
            log.exception("job timed out", extra={"job_id": job_id})
            return {"job_id": job_id, "status": "failed"}
        except Exception as exc:  # noqa: BLE001
            fail_job(conn, job_id, f"{type(exc).__name__}: {exc}")
            log.exception("job failed", extra={"job_id": job_id})
            return {"job_id": job_id, "status": "failed"}

        log.info("job done", extra={"job_id": job_id, "plies": result["plies"]})
        return result

    def run(self, *, stop_after: int | None = None) -> int:
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
                        rep = reap_stale_jobs(conn, timeout_seconds=timeout)
                        if rep.get("requeued") or rep.get("failed"):
                            log.info("reaper swept stale jobs", extra=rep)
                    except Exception:  # noqa: BLE001
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
