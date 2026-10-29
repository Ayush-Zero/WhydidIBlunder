"""C7 — per-IP rate limiting at the HTTP boundary.

The limiter itself is unit-tested in ``test_ratelimit.py``. These tests pin the
integration: the sixth analysis is a ``429`` with a ``Retry-After``, the limit is
per client IP, a spoofed ``X-Forwarded-For`` from an untrusted peer is ignored,
and no other endpoint is affected.

Offline throughout: the Chess.com client is a mock transport over recorded
fixtures, and the limiter's clock is injected so no test sleeps.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from server import chesscom
from server.app import create_app
from server.chesscom import ChessComClient, game_key
from server.ratelimit import RateLimiter

FIXTURES = Path(__file__).parent / "fixtures" / "chesscom"
ARCHIVES_URL = f"{chesscom.API_BASE}/player/beast-ash/games/archives"
JUL = f"{chesscom.API_BASE}/player/beast-ash/games/2026/07"
AUG = f"{chesscom.API_BASE}/player/beast-ash/games/2026/08"
SEP = f"{chesscom.API_BASE}/player/beast-ash/games/2026/09"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


ARCHIVES = _load("archives.json")
LIVE_MONTH = _load("month_live.json")
EDGE_MONTH = _load("month_edge_cases.json")
WHITE_GAME = LIVE_MONTH["games"][1]
WHITE_KEY = game_key(WHITE_GAME)
PAYLOAD = {"username": "beast-ash", "game_key": WHITE_KEY}


class Router:
    """Routes requests to recorded responses; anything unlisted is a 404."""

    def __init__(self, routes: dict[str, tuple[int, object]]):
        self.routes = routes
        self.hits: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.hits.append(url)
        if url in self.routes:
            status, payload = self.routes[url]
            return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"code": 404})


def standard_routes() -> dict[str, tuple[int, object]]:
    return {
        ARCHIVES_URL: (200, ARCHIVES),
        JUL: (200, EDGE_MONTH),
        AUG: (200, {"games": []}),
        SEP: (200, LIVE_MONTH),
    }


class Clock:
    """A monotonic clock the test can advance by hand."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@contextmanager
def running_app(
    tmp_path,
    *,
    limit: int,
    window: float = 3600.0,
    trusted: str = "",
    client_address: tuple[str, int] = ("testclient", 50000),
):
    """A booted app with an offline client, a fake clock and a temp database."""
    router = Router(standard_routes())
    chesscom_client = ChessComClient(
        transport=httpx.MockTransport(router),
        cache_dir=tmp_path / "chesscom_cache",
        now=lambda: 1_000.0,
        sleep=lambda _seconds: None,
        pause=0.0,
    )
    clock = Clock()
    limiter = RateLimiter(limit=limit, window_seconds=window, clock=clock)
    app = create_app(
        configure_logging_now=False,
        chesscom_client=chesscom_client,
        db_path=str(tmp_path / "api.db"),
        rate_limiter=limiter,
        trusted_proxies=trusted,
    )
    with TestClient(app, client=client_address) as test_client:
        yield test_client, clock, router


def _analyze(client: TestClient, **headers):
    return client.post("/api/analyze", json=PAYLOAD, headers=headers or None)


# --- the limit ------------------------------------------------------------


def test_the_sixth_analysis_request_is_rate_limited(tmp_path):
    with running_app(tmp_path, limit=5) as (client, _clock, _router):
        allowed = [_analyze(client) for _ in range(5)]
        denied = _analyze(client)

    assert [response.status_code for response in allowed] == [202, 200, 200, 200, 200]
    assert denied.status_code == 429
    assert denied.json()["detail"] == "rate limit exceeded; try again later"
    assert denied.headers["X-RateLimit-Limit"] == "5"
    assert denied.headers["X-RateLimit-Remaining"] == "0"
    assert int(denied.headers["X-RateLimit-Reset"]) >= 1
    assert int(denied.headers["Retry-After"]) >= 1


def test_allowed_responses_carry_the_rate_limit_headers(tmp_path):
    with running_app(tmp_path, limit=2) as (client, _clock, _router):
        response = _analyze(client)

    assert response.status_code == 202
    assert response.headers["X-RateLimit-Limit"] == "2"
    assert response.headers["X-RateLimit-Remaining"] == "1"
    assert int(response.headers["X-RateLimit-Reset"]) >= 1


def test_the_limit_is_per_client_ip_behind_a_trusted_proxy(tmp_path):
    with running_app(
        tmp_path,
        limit=1,
        trusted="10.0.0.0/8",
        client_address=("10.0.0.2", 1234),
    ) as (client, _clock, _router):
        first = _analyze(client, **{"X-Forwarded-For": "203.0.113.1"})
        same_client = _analyze(client, **{"X-Forwarded-For": "203.0.113.1"})
        other_client = _analyze(client, **{"X-Forwarded-For": "203.0.113.2"})

    assert first.status_code == 202
    assert same_client.status_code == 429
    assert other_client.status_code != 429


def test_a_spoofed_xff_from_an_untrusted_peer_is_ignored(tmp_path):
    # The TestClient peer is not an IP, so it is not a trusted proxy; changing
    # X-Forwarded-For must not create a fresh bucket.
    with running_app(tmp_path, limit=1, trusted="") as (client, _clock, _router):
        first = _analyze(client, **{"X-Forwarded-For": "203.0.113.1"})
        spoofed = _analyze(client, **{"X-Forwarded-For": "203.0.113.2"})

    assert first.status_code == 202
    assert spoofed.status_code == 429


def test_the_window_expires(tmp_path):
    with running_app(tmp_path, limit=1, window=60) as (client, clock, _router):
        assert _analyze(client).status_code == 202
        assert _analyze(client).status_code == 429
        clock.advance(60.1)
        assert _analyze(client).status_code != 429


def test_a_rejected_body_does_not_consume_the_quota(tmp_path):
    with running_app(tmp_path, limit=1) as (client, _clock, _router):
        invalid = client.post(
            "/api/analyze", json={"username": "beast-ash"}
        )
        first = _analyze(client)
        second = _analyze(client)

    assert invalid.status_code == 422
    assert first.status_code == 202
    assert second.status_code == 429


def test_the_limiter_runs_before_chesscom(tmp_path):
    with running_app(tmp_path, limit=1) as (client, _clock, router):
        assert _analyze(client).status_code == 202
        hits_after_first = len(router.hits)
        assert _analyze(client).status_code == 429
        assert len(router.hits) == hits_after_first


def test_other_endpoints_are_not_rate_limited(tmp_path):
    with running_app(tmp_path, limit=1) as (client, _clock, _router):
        assert _analyze(client).status_code == 202
        assert _analyze(client).status_code == 429
        explain = client.post(
            "/api/explain",
            json={
                "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                "played": "e2e4",
                "best": "d2d4",
            },
        )

    assert explain.status_code == 200


def test_cors_exposes_the_rate_limit_headers(tmp_path, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173")
    with running_app(tmp_path, limit=2) as (client, _clock, _router):
        response = _analyze(client, Origin="http://localhost:5173")

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    exposed = response.headers["access-control-expose-headers"]
    assert "Retry-After" in exposed
    assert "X-RateLimit-Remaining" in exposed


def test_zero_disables_the_limiter(tmp_path):
    with running_app(tmp_path, limit=0) as (client, _clock, _router):
        responses = [_analyze(client) for _ in range(6)]

    assert all(response.status_code != 429 for response in responses)
    assert "X-RateLimit-Limit" not in responses[0].headers
