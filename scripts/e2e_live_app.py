"""Opt-in full-stack harness for the Playwright live smoke test.

This boots the real FastAPI app and a real worker process with Stockfish. Only
Chess.com is replaced by the recorded fixtures in ``tests/fixtures/chesscom``,
so the smoke is deterministic and never touches the network.

The worker starts when the browser queues a job and exits after that one job.
That keeps the process tree bounded even though Playwright owns the shutdown.

Run it through ``web/playwright.config.ts`` with ``RUN_LIVE_TESTS=1``; do not
start it by hand unless debugging the harness.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "chesscom"
LIVE_API_PORT = 8001


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


ARCHIVES = _load("archives.json")
LIVE_MONTH = _load("month_live.json")
EDGE_MONTH = _load("month_edge_cases.json")


def _configure_environment(db_path: Path) -> None:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")

    # The live smoke must be quick and must not inherit a developer's cache.
    # Cache correctness has its own tests; this one is about the process seams.
    os.environ["ANALYSIS_DEPTH"] = "10"
    os.environ["ANALYSIS_MULTIPV"] = "2"
    os.environ["STOCKFISH_THREADS"] = "1"
    os.environ["STOCKFISH_HASH_MB"] = "128"
    os.environ["JOB_TIMEOUT_SECONDS"] = "180"
    os.environ["REAPER_INTERVAL_SECONDS"] = "0"
    os.environ["RATE_LIMIT_ANALYSES_PER_HOUR"] = "0"
    os.environ["RATE_LIMIT_TRY_PER_HOUR"] = "0"
    os.environ["DB_PATH"] = str(db_path)
    os.environ.pop("CACHE_DIR", None)
    os.environ.pop("CHESSCOM_CACHE_DIR", None)


def _fixture_transport():
    import httpx

    from server import chesscom

    routes = {
        f"{chesscom.API_BASE}/player/beast-ash/games/archives": (200, ARCHIVES),
        f"{chesscom.API_BASE}/player/beast-ash/games/2026/07": (200, EDGE_MONTH),
        f"{chesscom.API_BASE}/player/beast-ash/games/2026/08": (
            200,
            {"games": []},
        ),
        f"{chesscom.API_BASE}/player/beast-ash/games/2026/09": (200, LIVE_MONTH),
    }

    def handle(request: httpx.Request) -> httpx.Response:
        status, payload = routes.get(str(request.url), (404, {"code": 404}))
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handle)


def _run_one_job_when_queued(db_path: Path) -> None:
    from server import db

    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        connection = db.connect(db_path)
        try:
            queued = connection.execute(
                "SELECT 1 FROM jobs WHERE status = 'queued' LIMIT 1"
            ).fetchone()
        finally:
            connection.close()

        if queued is not None:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "server.worker_main",
                    "--db",
                    str(db_path),
                    "--stop-after=1",
                    "--sleep=0.05",
                ],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                check=False,
            )
            return

        time.sleep(0.05)


def main() -> None:
    sys.path.insert(0, str(REPO_ROOT))

    temp_dir = Path(tempfile.mkdtemp(prefix="wib-live-e2e-"))
    db_path = temp_dir / "e2e.db"
    _configure_environment(db_path)

    import uvicorn

    from server import db
    from server.app import create_app
    from server.chesscom import ChessComClient

    connection = db.connect(db_path)
    try:
        db.init_db(connection)
    finally:
        connection.close()

    client = ChessComClient(
        transport=_fixture_transport(),
        cache_dir=None,
        sleep=lambda _seconds: None,
        pause=0.0,
    )
    app = create_app(
        configure_logging_now=False,
        chesscom_client=client,
        db_path=str(db_path),
    )

    worker_thread = threading.Thread(
        target=_run_one_job_when_queued,
        args=(db_path,),
        daemon=True,
    )
    worker_thread.start()

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=LIVE_API_PORT,
            log_config=None,
            access_log=False,
        )
    )
    try:
        server.run()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
