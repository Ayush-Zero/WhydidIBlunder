"""C1 — ``GET /api/player/{username}/games``, over recorded Chess.com JSON.

These are API tests, not client tests: they pin the *normalised contract* the
browser will consume. No network and no engine — the app is built with an
injected ``ChessComClient`` whose transport serves ``tests/fixtures/chesscom``.

The interesting checks are the policy defaults (bots in, from-position out),
the case-insensitive ``user_color``, and that a repeat request is a disk read
rather than a second trip upstream.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from server import chesscom
from server.app import create_app
from server.chesscom import ChessComClient, game_key

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
LIVE_GAMES = LIVE_MONTH["games"]


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
    """Three archives: an edge-case month, an empty month, and a normal one."""
    return {
        ARCHIVES_URL: (200, ARCHIVES),
        JUL: (200, EDGE_MONTH),
        AUG: (200, {"games": []}),
        SEP: (200, LIVE_MONTH),
    }


@contextmanager
def running_app(routes, *, cache_dir=None):
    """A booted app with an injected, offline Chess.com client."""
    router = Router(routes)
    client = ChessComClient(
        transport=httpx.MockTransport(router),
        cache_dir=cache_dir,
        now=lambda: 1_000.0,
        sleep=lambda _seconds: None,
        pause=0.0,
    )
    app = create_app(configure_logging_now=False, chesscom_client=client)
    with TestClient(app) as test_client:
        yield test_client, router


# --- the happy path -------------------------------------------------------


def test_game_list_is_normalised_and_newest_first():
    with running_app(standard_routes()) as (client, _):
        response = client.get("/api/player/beast-ash/games")

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "beast-ash"
    assert body["count"] == len(body["games"]) == 7

    first = body["games"][0]
    # September's games are listed oldest-first upstream; the API reverses them.
    assert first["game_key"] == game_key(LIVE_GAMES[1])
    assert first["white"]["username"] == "BEAST-ASH"
    assert first["black"]["username"] == "Gopinath_27"
    assert first["result"] == "1-0"
    assert first["time_class"] == "rapid"
    assert first["time_control"] == "600"
    assert first["rated"] is True
    assert first["is_bot"] is False
    assert first["from_position"] is False

    second = body["games"][1]
    assert second["game_key"] == game_key(LIVE_GAMES[0])
    assert second["result"] == "0-1"
    assert second["black"]["result"] == "win"


def test_user_color_is_resolved_case_insensitively():
    """The request said ``beast-ash``; Chess.com returned ``BEAST-ASH``."""
    with running_app(standard_routes()) as (client, _):
        games = client.get("/api/player/beast-ash/games", params={"months": 1}).json()["games"]

    assert [game["user_color"] for game in games] == ["white", "black"]
    # Both sides are present in the normalised player objects.
    assert games[0]["white"]["rating"] == 1171
    assert games[1]["black"]["rating"] == 1153


def test_user_color_is_none_when_the_player_is_not_in_the_game():
    """Defensive: an upstream game that omits the requested player is not a crash."""
    with running_app(standard_routes()) as (client, _):
        games = client.get("/api/player/beast-ash/games").json()["games"]

    # July's fixture games are between other players entirely.
    assert any(game["user_color"] is None for game in games)


def test_the_pgn_is_not_part_of_the_list_payload():
    """The list is fetched on every page load; the PGN belongs to C2."""
    with running_app(standard_routes()) as (client, _):
        response = client.get("/api/player/beast-ash/games")

    assert "pgn" not in response.json()["games"][0]
    assert "pgn" not in response.text


def test_every_game_carries_a_key_and_both_players():
    with running_app(standard_routes()) as (client, _):
        games = client.get("/api/player/beast-ash/games").json()["games"]

    assert all(game["game_key"] for game in games)
    assert all(game["white"]["username"] for game in games)
    assert all(game["black"]["username"] for game in games)


# --- policy defaults ------------------------------------------------------


def test_from_position_games_are_excluded_by_default():
    with running_app(standard_routes()) as (client, _):
        games = client.get("/api/player/beast-ash/games").json()["games"]

    assert games and not any(game["from_position"] for game in games)


def test_from_position_games_can_be_included():
    with running_app(standard_routes()) as (client, _):
        response = client.get(
            "/api/player/beast-ash/games", params={"include_from_position": "true"}
        )

    games = response.json()["games"]
    assert response.json()["count"] == 10
    assert sum(game["from_position"] for game in games) == 3


def test_bots_are_included_by_default():
    with running_app(standard_routes()) as (client, _):
        games = client.get("/api/player/beast-ash/games").json()["games"]

    assert any(game["is_bot"] for game in games)


def test_bots_can_be_filtered_out():
    with running_app(standard_routes()) as (client, _):
        response = client.get(
            "/api/player/beast-ash/games", params={"include_bots": "false"}
        )

    games = response.json()["games"]
    assert response.json()["count"] == 5
    assert not any(game["is_bot"] for game in games)


# --- query parameters -----------------------------------------------------


def test_months_limits_which_archives_are_consulted():
    with running_app(standard_routes()) as (client, _):
        response = client.get("/api/player/beast-ash/games", params={"months": 1})

    assert response.json()["count"] == 2


def test_max_games_stops_after_the_newest_n():
    with running_app(standard_routes()) as (client, _):
        response = client.get("/api/player/beast-ash/games", params={"max_games": 1})

    games = response.json()["games"]
    assert response.json()["count"] == 1
    assert games[0]["game_key"] == game_key(LIVE_GAMES[1])


def test_time_class_filter():
    with running_app(standard_routes()) as (client, _):
        response = client.get(
            "/api/player/beast-ash/games", params={"time_class": "daily"}
        )

    games = response.json()["games"]
    assert games and all(game["time_class"] == "daily" for game in games)


@pytest.mark.parametrize(
    "params",
    [
        {"months": 0},
        {"max_games": 0},
        {"time_class": "ultra"},
    ],
)
def test_invalid_query_parameters_are_rejected(params):
    with running_app(standard_routes()) as (client, _):
        assert client.get("/api/player/beast-ash/games", params=params).status_code == 422


def test_a_username_outside_the_allowed_pattern_is_rejected():
    with running_app(standard_routes()) as (client, router):
        assert client.get("/api/player/bad%20name/games").status_code == 422
    assert router.hits == [], "a rejected username must not reach Chess.com"


# --- failures -------------------------------------------------------------


def test_unknown_player_is_404():
    with running_app({}) as (client, _):
        response = client.get("/api/player/nobody-here/games")

    assert response.status_code == 404
    assert "nobody-here" in response.json()["detail"]


def test_upstream_failure_is_502_not_500():
    """A Chess.com outage is a bad gateway, not a bug in this service."""
    with running_app({ARCHIVES_URL: (429, {"code": 429})}) as (client, _):
        response = client.get("/api/player/beast-ash/games")

    assert response.status_code == 502
    assert "chess.com" in response.json()["detail"].lower()


# --- caching --------------------------------------------------------------


def test_repeat_request_is_served_from_the_disk_cache(tmp_path):
    routes = standard_routes()
    with running_app(routes, cache_dir=tmp_path) as (client, router):
        assert client.get("/api/player/beast-ash/games").status_code == 200
        first_hits = list(router.hits)

        assert client.get("/api/player/beast-ash/games").status_code == 200
        assert router.hits == first_hits, "the second call must not touch the network"

    assert len(first_hits) == 4  # archives + three months
