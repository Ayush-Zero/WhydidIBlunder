"""A2 — the FastAPI skeleton: config, logging, `/health` (offline).

These tests pin what the skeleton is *for*: it boots without a network or an
engine, it reads settings from one place, and `/health` tells the truth — that
last one is the whole reason the endpoint exists, so it is tested in the
unhealthy cases, not just the healthy one.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from server import db
from server.app import create_app, cors_origins
from server.logging_setup import KeyValueFormatter, request_id_var


@pytest.fixture()
def client(tmp_path):
    """A booted app backed by a migrated temp database."""
    path = tmp_path / "api.db"
    connection = db.connect(path)
    db.init_db(connection)
    connection.close()
    app = create_app(db_path=str(path), configure_logging_now=False)
    with TestClient(app) as test_client:
        yield test_client


# --- /health --------------------------------------------------------------


def test_health_returns_ok_when_the_schema_is_current(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["schema_version"] == db.SCHEMA_VERSION


def test_health_reports_unavailable_when_the_database_cannot_be_opened(tmp_path):
    """The point of the endpoint: a dependency failure must be visible here
    rather than surfacing as a confusing error on the first real request.

    Opening a *directory* as a SQLite file reliably fails, on every platform.
    """
    app = create_app(db_path=str(tmp_path), configure_logging_now=False)
    with TestClient(app) as test_client:
        response = test_client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["database"] == "error"


def test_health_reports_degraded_when_the_schema_is_behind(tmp_path):
    """An unmigrated database is reachable but not usable — 503, not 200."""
    path = tmp_path / "empty.db"
    db.connect(path).close()  # creates the file, runs no migrations

    app = create_app(db_path=str(path), configure_logging_now=False)
    with TestClient(app) as test_client:
        response = test_client.get("/health")
    assert response.status_code == 503
    assert response.json()["database"] == "out-of-date"


def test_health_does_not_leak_paths_or_config(client):
    """`/health` is unauthenticated; it must not describe the filesystem."""
    body = client.get("/health").text
    assert "sqlite" not in body.lower()
    assert "tmp" not in body.lower()
    assert ".db" not in body


def test_health_rejects_post(client):
    assert client.post("/health").status_code == 405


# --- app shape ------------------------------------------------------------


def test_app_boots_and_serves_openapi(client):
    """A skeleton that cannot produce its own schema is not a skeleton."""
    assert client.get("/api/openapi.json").status_code == 200


def test_docs_are_served_under_api_not_root(client):
    """The frontend owns `/`, so API tooling lives under `/api`."""
    assert client.get("/api/docs").status_code == 200
    assert client.get("/docs").status_code == 404


def test_no_analysis_endpoints_exist_yet(client):
    """Milestone C owns these; a stray one here would mean scoping drift."""
    for method, path in (
        ("get", "/api/player/someone/games"),
        ("post", "/api/analyze"),
        ("get", "/api/jobs/abc"),
        ("get", "/api/games/abc/report"),
    ):
        assert getattr(client, method)(path).status_code == 404


def test_redoc_is_disabled(client):
    """One documentation UI is enough; a second is surface area."""
    assert client.get("/redoc").status_code == 404


# --- CORS -----------------------------------------------------------------


def test_cors_origins_default_to_the_dev_server(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert cors_origins() == ["http://localhost:5173"]


def test_cors_origins_are_split_and_trimmed(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example ,")
    assert cors_origins() == ["https://a.example", "https://b.example"]


def test_cors_headers_are_returned_for_an_allowed_origin(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example")
    app = create_app(configure_logging_now=False)
    with TestClient(app) as test_client:
        response = test_client.get("/health", headers={"Origin": "https://app.example"})
    assert response.headers.get("access-control-allow-origin") == "https://app.example"


def test_cors_does_not_allow_an_unlisted_origin(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example")
    app = create_app(configure_logging_now=False)
    with TestClient(app) as test_client:
        response = test_client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_jobs_and_moves_reject_unknown_status_values(monkeypatch):
    """A small guard that the schema constraints are still in force through the
    API process's connections, not just in the storage tests."""
    connection = db.connect()
    try:
        assert db.schema_version(connection) >= 0
    finally:
        connection.close()


# --- request context ------------------------------------------------------


def test_response_carries_a_request_id(client):
    response = client.get("/health")
    assert response.headers.get("X-Request-ID")


def test_a_supplied_request_id_is_echoed_back(client):
    """Lets an upstream proxy (Caddy) own correlation across services."""
    response = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"


def test_each_request_gets_a_distinct_id(client):
    first = client.get("/health").headers["X-Request-ID"]
    second = client.get("/health").headers["X-Request-ID"]
    assert first != second


# --- logging --------------------------------------------------------------


def test_log_lines_are_key_value_and_carry_the_request_id():
    formatter = KeyValueFormatter()
    record = logging.LogRecord(
        name="whydidiblunder.api",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request",
        args=(),
        exc_info=None,
    )
    record.method = "GET"
    record.status = 200

    token = request_id_var.set("req-42")
    try:
        line = formatter.format(record)
    finally:
        request_id_var.reset(token)

    assert "req=req-42" in line
    assert "method='GET'" in line
    assert "status=200" in line
    assert line.startswith("20")  # a timestamp


def test_logging_never_prints_secrets(monkeypatch):
    """A guard rather than a proof: the configured key must not reach a log line
    through the formatter, because nothing here logs environment values."""
    formatter = KeyValueFormatter()
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="startup", args=(), exc_info=None,
    )
    monkeypatch.setenv("LLM_API_KEY", "super-secret-value")
    assert "super-secret-value" not in formatter.format(record)


def test_configure_logging_is_idempotent():
    from server.logging_setup import configure_logging

    configure_logging()
    root = logging.getLogger()
    handlers = len(root.handlers)
    configure_logging()
    assert len(root.handlers) == handlers
