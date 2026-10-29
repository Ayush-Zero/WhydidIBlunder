"""C6 — ``POST /api/explain``: the Phase 2 stub.

The route exists so the frontend can wire the call; the LLM layer is Phase 5.
These tests pin the stub's boundaries:

* a valid request gets a placeholder, clearly labelled ``stub: true``;
* the request shape is the Phase 5 input (``fen``, ``played``, ``best``,
  ``pv``, ``tags``), with ``extra="forbid"``;
* missing/oversized fields are 422;
* the stub does not echo user-controlled strings;
* it does not need the database or Chess.com.

Offline and engine-free: no report is read and no LLM is called.
"""

from __future__ import annotations

from contextlib import contextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.chesscom import ChessComClient

BODY = {
    "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
    "played": "e4",
    "best": "d4",
    "pv": ["d4", "d5", "c4"],
    "tags": ["hanging_piece"],
}


@contextmanager
def running_app(tmp_path):
    """A booted app with a throwaway database; C6 never opens it."""
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


def test_the_stub_returns_a_placeholder(tmp_path):
    with running_app(tmp_path) as (client, _):
        response = client.post("/api/explain", json=BODY)

    assert response.status_code == 200
    body = response.json()
    assert body["stub"] is True
    assert body["explanation"]
    assert "placeholder" in body["explanation"]


def test_pv_and_tags_are_optional(tmp_path):
    minimal = {"fen": BODY["fen"], "played": "e4", "best": "d4"}
    with running_app(tmp_path) as (client, _):
        response = client.post("/api/explain", json=minimal)

    assert response.status_code == 200
    assert response.json()["stub"] is True


@pytest.mark.parametrize("field", ["fen", "played", "best"])
def test_each_required_fact_is_required(tmp_path, field):
    body = dict(BODY)
    body.pop(field)
    with running_app(tmp_path) as (client, _):
        response = client.post("/api/explain", json=body)

    assert response.status_code == 422


def test_an_unknown_field_is_rejected(tmp_path):
    """A typo must not silently drop the fact the explanation needs."""
    body = dict(BODY, pgn="1. e4 e5")
    with running_app(tmp_path) as (client, _):
        response = client.post("/api/explain", json=body)

    assert response.status_code == 422


def test_pv_must_be_a_list(tmp_path):
    body = dict(BODY, pv="d4 d5 c4")
    with running_app(tmp_path) as (client, _):
        response = client.post("/api/explain", json=body)

    assert response.status_code == 422


def test_pv_is_bounded(tmp_path):
    body = dict(BODY, pv=["e4"] * 17)
    with running_app(tmp_path) as (client, _):
        response = client.post("/api/explain", json=body)

    assert response.status_code == 422


def test_tags_are_bounded(tmp_path):
    body = dict(BODY, tags=["fork"] * 17)
    with running_app(tmp_path) as (client, _):
        response = client.post("/api/explain", json=body)

    assert response.status_code == 422


def test_an_empty_body_is_422(tmp_path):
    with running_app(tmp_path) as (client, _):
        response = client.post("/api/explain")

    assert response.status_code == 422


def test_get_is_not_allowed(tmp_path):
    with running_app(tmp_path) as (client, _):
        response = client.get("/api/explain")

    assert response.status_code == 405


def test_the_stub_does_not_echo_user_input(tmp_path):
    """The placeholder is fixed; it must not reflect a crafted SAN/tag."""
    marker = "xss-marker"
    body = dict(BODY, played=marker, tags=[marker])
    with running_app(tmp_path) as (client, _):
        response = client.post("/api/explain", json=body)

    assert response.status_code == 200
    assert marker not in response.json()["explanation"]


def test_the_stub_does_not_touch_the_database(tmp_path):
    """C6 is a route, not a read: it must work before any worker has migrated."""
    with running_app(tmp_path) as (client, db_path):
        response = client.post("/api/explain", json=BODY)
        assert not db_path.exists()

    assert response.status_code == 200
