"""Phase 2 A2: the FastAPI application factory.

Deliberately thin. This part exists to prove the service boots, reads its
settings from one place, logs in a shape a log aggregator can parse, and exposes
a health check that tells the truth about the database. Endpoints live in
:mod:`server.api` and are included here; C1–C7 are now mounted.

Two decisions worth stating:

* **A factory (`create_app`) rather than a module-level app.** Tests build an
  app with an injected database path; production builds one from ``.env``. A
  module-level global would make the database path a process-wide fact decided at
  import time, which is exactly the thing tests need to vary.

* **`/health` actually checks the database.** A health endpoint that returns
  ``{"status": "ok"}`` without touching a dependency is worse than no health
  endpoint: it reports health during exactly the outages you care about.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from analyzer.cache import PositionCache
from server import db
from server.api import api_router
from server.chesscom import ChessComClient, cache_dir_from_env
from server.logging_setup import configure_logging, request_id_var
from server.ratelimit import (
    RateLimiter,
    parse_trusted_proxies,
    rate_limit_per_hour_from_env,
    rate_limit_try_per_hour_from_env,
    trusted_proxies_from_env,
)
from server.try_grader import TryGrader, default_engine_factory
from server.worker import default_cache_for

__all__ = ["create_app", "cors_origins"]

log = logging.getLogger("whydidiblunder.api")

#: Paths that must never be rate limited, cached, or require the database.
HEALTH_PATH = "/health"


def cors_origins() -> list[str]:
    """Allowed browser origins, from ``CORS_ORIGINS`` (comma-separated).

    Defaults to the Vite dev server. In production Caddy serves the built
    frontend from the same origin, so this list should shrink to that origin (or
    to nothing) rather than staying wide.
    """
    raw = os.getenv("CORS_ORIGINS", "http://localhost:5173")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app(
    *,
    db_path: str | None = None,
    configure_logging_now: bool = True,
    chesscom_client: ChessComClient | None = None,
    rate_limiter: RateLimiter | None = None,
    trusted_proxies: str | None = None,
    try_rate_limiter: RateLimiter | None = None,
    try_engine_factory: Callable[[], object] | None = None,
    try_cache_factory: Callable[[sqlite3.Connection], PositionCache] | None = None,
    try_timeout_seconds: float | None = None,
) -> FastAPI:
    """Build the application.

    Parameters
    ----------
    db_path:
        Override the database location (tests pass a temp file). ``None`` means
        "read it from config", which is what production wants.
    configure_logging_now:
        Logging is configured once per *process*, not once per app, so tests that
        build several apps can skip re-configuring the root logger.
    chesscom_client:
        Override the Chess.com client. Tests inject one backed by recorded JSON
        so the API tests never touch the network. ``None`` builds the production
        client, using ``CHESSCOM_CACHE_DIR`` when it is set.
    rate_limiter:
        Override the per-IP limiter. ``None`` builds one from
        ``RATE_LIMIT_ANALYSES_PER_HOUR``; pass ``RateLimiter(limit=0)`` to
        disable it explicitly.
    trusted_proxies:
        Comma-separated IPs/CIDRs allowed to set ``X-Forwarded-For``. ``None``
        reads ``TRUSTED_PROXY_IPS`` (loopback by default).
    try_rate_limiter:
        Separate per-IP bucket for the try-again endpoint. ``None`` builds one
        from ``RATE_LIMIT_TRY_PER_HOUR``; ``RateLimiter(limit=0)`` disables it.
    try_engine_factory / try_cache_factory / try_timeout_seconds:
        Test seams for the engine-in-API grader. Production leaves them unset:
        the factory uses Stockfish from config, the cache is the worker's
        durable ``position_eval`` cache, and the timeout comes from
        ``TRY_TIMEOUT_SECONDS``.
    """
    if configure_logging_now:
        configure_logging()

    client = (
        chesscom_client
        if chesscom_client is not None
        else ChessComClient(cache_dir=cache_dir_from_env())
    )
    limiter = (
        rate_limiter
        if rate_limiter is not None
        else RateLimiter(limit=rate_limit_per_hour_from_env())
    )
    try_limiter = (
        try_rate_limiter
        if try_rate_limiter is not None
        else RateLimiter(limit=rate_limit_try_per_hour_from_env())
    )
    grader = TryGrader(
        engine_factory=try_engine_factory or default_engine_factory,
        cache_factory=try_cache_factory or default_cache_for,
        timeout_seconds=try_timeout_seconds,
    )
    proxies = (
        trusted_proxies_from_env()
        if trusted_proxies is None
        else parse_trusted_proxies(trusted_proxies)
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # The app owns the client, so it closes the connection pool on shutdown.
        # Close is idempotent, which keeps an injected client's owner free to
        # close it too.
        yield
        client.close()

    app = FastAPI(
        title="WhydidIBlunder API",
        version="0.1.0",
        description="Analyse Chess.com games with Stockfish.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,  # nothing here needs a second documentation UI
        lifespan=lifespan,
    )
    # The routes read the client from here; there is exactly one per process.
    app.state.chesscom = client
    # ``None`` means "read it from config" on every connection, so C2 and
    # /health cannot disagree about which database they mean.
    app.state.db_path = db_path
    # C7: the limiter and the proxy trust list are process-wide facts, like the
    # client. Tests inject both; production builds them from env.
    app.state.rate_limiter = limiter
    app.state.try_rate_limiter = try_limiter
    app.state.try_grader = grader
    app.state.trusted_proxies = proxies

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_credentials=False,  # there is no auth to carry
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        # C7's headers are only useful to browser code if CORS exposes them;
        # without this the UI can see the 429 status but not the retry delay.
        expose_headers=[
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ],
    )

    @app.middleware("http")
    async def _request_context(request: Request, call_next: Callable):
        """Attach a request id and log the outcome with a duration.

        The request id is stored in a :class:`contextvars.ContextVar` rather than
        passed down as an argument, so every log line emitted anywhere during the
        request carries it without the call chain having to thread it through.
        That is what makes a multi-line trace reconstructable from logs alone.
        """
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Log the failure with its id, then let FastAPI's handler produce the
            # response body — the id is what correlates the two.
            log.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            raise
        finally:
            request_id_var.reset(token)

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers["X-Request-ID"] = request_id
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response

    @app.get(HEALTH_PATH, tags=["meta"])
    def health() -> JSONResponse:
        """Liveness **and** a real database check.

        Returns ``200`` only when the schema can actually be read. A worker that
        cannot open the database is not healthy, and saying otherwise just moves
        the failure to the first user request.

        Deliberately reports no paths, versions or configuration values — a
        health endpoint is unauthenticated and should not leak the filesystem
        layout.
        """
        try:
            connection = db.connect(db_path)
            try:
                version = db.schema_version(connection)
            finally:
                connection.close()
        except Exception as exc:  # noqa: BLE001 - report, never raise from /health
            log.error("health check failed", extra={"error": type(exc).__name__})
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database": "error"},
            )

        current = version == db.SCHEMA_VERSION
        return JSONResponse(
            status_code=200 if current else 503,
            content={
                "status": "ok" if current else "degraded",
                "database": "ok" if current else "out-of-date",
                "schema_version": version,
            },
        )

    app.include_router(api_router)
    return app
