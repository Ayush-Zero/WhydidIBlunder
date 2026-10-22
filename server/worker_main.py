"""``python -m server.worker_main`` — run a worker process.

The worker is a **separate process** from the API on purpose: Stockfish is
CPU-bound and would otherwise block the event loop serving requests. Scaling is
"start more of these", with no change to the code (B2's claim makes concurrent
workers safe).

    .\\.venv\\Scripts\\python.exe -m server.worker_main
    .\\.venv\\Scripts\\python.exe -m server.worker_main --stop-after=1
"""

from __future__ import annotations

import argparse
import logging

from server import db
from server.logging_setup import configure_logging
from server.worker import Worker

log = logging.getLogger("whydidiblunder.worker")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the analysis worker")
    parser.add_argument(
        "--stop-after",
        type=int,
        default=None,
        help="exit after running this many jobs (drains a queue and stops)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="database path (default: from .env, see server.db.default_db_path)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="seconds to wait when the queue is empty",
    )
    args = parser.parse_args(argv)

    configure_logging()
    worker = Worker(db_path=args.db, sleep_seconds=args.sleep)
    log.info("worker started", extra={"worker_id": worker.worker_id, "db": worker.db_path})
    try:
        count = worker.run(stop_after=args.stop_after)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        log.info("worker interrupted")
        return 130
    log.info("worker stopped", extra={"jobs_run": count})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
