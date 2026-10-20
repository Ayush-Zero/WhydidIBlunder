"""A3 — the Chess.com client, over recorded JSON (offline: no network).

The breakdown asks for unit tests over recorded responses covering every edge
case, so each one gets a named test:

* empty months (404) are normal, not an error;
* bot games are detected by either signal and are filterable;
* "from position" games are detected by ``type``, by ``initial_setup``, or by a
  ``[FEN ...]`` tag in the PGN — and are excluded by default;
* games with no ``uuid`` key off ``url``, then ``link``, then a digest;
* responses are cached on disk, with a TTL for the ones that still change.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from server import chesscom
from server.chesscom import (
    ChessComClient,
    PlayerNotFound,
    game_key,
    is_bot,
    is_from_position,
)

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

#: ``month_edge_cases.json`` games, addressable by the white player's name.
EDGE_GAMES = {
    game["white"]["username"]: game for game in EDGE_MONTH["games"] if game.get("white")
}
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


class Clock:
    """A controllable clock so TTL behaviour can be tested without waiting."""

    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


class Sleeper:
    """Records the politeness pauses instead of sleeping through them."""

    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def make_client(routes, *, cache_dir=None, clock=None, pause=0.0):
    router = Router(routes)
    sleeper = Sleeper()
    client = ChessComClient(
        transport=httpx.MockTransport(router),
        cache_dir=cache_dir,
        now=clock or Clock(),
        sleep=sleeper,
        pause=pause,
    )
    return client, router, sleeper


def standard_routes() -> dict[str, tuple[int, object]]:
    """Three archives: an edge-case month, an empty month, and a normal one."""
    return {
        ARCHIVES_URL: (200, ARCHIVES),
        JUL: (200, EDGE_MONTH),
        AUG: (200, {"games": []}),
        SEP: (200, LIVE_MONTH),
    }


# --- pure predicates ------------------------------------------------------


def test_cache_dir_env_is_optional(monkeypatch):
    """Unset or empty disables caching; a path enables it."""
    monkeypatch.delenv(chesscom.CACHE_DIR_ENV, raising=False)
    assert chesscom.cache_dir_from_env() is None
    monkeypatch.setenv(chesscom.CACHE_DIR_ENV, "   ")
    assert chesscom.cache_dir_from_env() is None
    monkeypatch.setenv(chesscom.CACHE_DIR_ENV, "./data/chesscom_cache")
    assert chesscom.cache_dir_from_env() == "./data/chesscom_cache"


def test_human_game_is_not_a_bot():
    assert not any(is_bot(game) for game in LIVE_GAMES)


def test_bot_detected_by_user_title():
    assert is_bot(EDGE_GAMES["BotByTitle"])


def test_bot_detected_by_username_suffix():
    """Some bot accounts omit ``user_title``; the ``--bot`` suffix catches them."""
    game = EDGE_GAMES["BotBySuffix"]
    assert not is_bot({"white": game["white"]})  # the human side alone is clean
    assert is_bot(game)


def test_standard_live_game_is_not_from_position():
    """Live games carry ``type: null``, which must not be mistaken for a marker."""
    assert not any(is_from_position(game) for game in LIVE_GAMES)
    assert not is_from_position(EDGE_GAMES["NoUuidUrl"])


def test_from_position_detected_by_type():
    assert is_from_position(EDGE_GAMES["FromPosByType"])


def test_from_position_detected_by_initial_setup():
    assert is_from_position(EDGE_GAMES["FromPosBySetup"])


def test_from_position_detected_by_pgn_tag():
    assert is_from_position(EDGE_GAMES["FromPosByPgn"])


def test_standard_fen_tag_is_not_from_position():
    """A ``[FEN ...]`` tag naming the *standard* start is not a custom setup."""
    pgn = '[SetUp "1"]\n[FEN "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"]\n\n1. e4 *'
    assert not is_from_position({"pgn": pgn})


def test_from_position_check_survives_malformed_fen_tag():
    assert not is_from_position({"pgn": '[FEN "unterminated'})


# --- game keys ------------------------------------------------------------


def test_key_prefers_uuid():
    assert game_key(LIVE_GAMES[0]) == f"chesscom:{LIVE_GAMES[0]['uuid']}"


def test_key_falls_back_to_url():
    game = EDGE_GAMES["NoUuidUrl"]
    assert "uuid" not in game
    assert game_key(game) == f"chesscom-url:{game['url']}"


def test_key_falls_back_to_link():
    game = EDGE_GAMES["NoUuidLink"]
    assert "uuid" not in game and "url" not in game
    assert game_key(game) == f"chesscom-url:{game['link']}"


def test_key_derives_when_no_identifier():
    game = EDGE_GAMES["NoIdentifier"]
    key = game_key(game)
    assert key.startswith("derived:")
    # Stable across calls: a duplicate key would create a duplicate report.
    assert game_key(dict(game)) == key


def test_every_edge_case_game_yields_a_key():
    games = LIVE_MONTH["games"] + EDGE_MONTH["games"]
    keys = [game_key(game) for game in games]
    assert all(keys)
    assert len(set(keys)) == len(keys), "keys must be unique across a month"


# --- the client -----------------------------------------------------------


def test_list_archives_is_oldest_first():
    client, router, _ = make_client(standard_routes())
    assert client.list_archives("beast-ash") == ARCHIVES["archives"]
    assert router.hits == [ARCHIVES_URL]


def test_unknown_player_raises_player_not_found():
    client, _, _ = make_client({ARCHIVES_URL: (404, {"code": 404})})
    with pytest.raises(PlayerNotFound):
        client.list_archives("nobody-here")


def test_player_with_no_games_is_not_an_error():
    """A real account with zero games answers 200 with an empty archive list."""
    client, _, _ = make_client({ARCHIVES_URL: (200, {"archives": []})})
    assert client.list_archives("beast-ash") == []


def test_empty_month_404_yields_no_games():
    """chess.com answers 404 — not an empty list — for a month with no games."""
    client, _, _ = make_client(standard_routes())
    assert client.month_games(AUG) == []


def test_recent_games_newest_first():
    client, _, _ = make_client(standard_routes())
    games = client.recent_games("beast-ash")
    # chess.com lists a month's games oldest-first; we return newest-first.
    assert [game_key(g) for g in games[:2]] == [
        game_key(g) for g in reversed(LIVE_GAMES)
    ]
    # July contributes everything that still has a PGN and starts from move 1,
    # in reverse API order: the no-PGN game and the three from-position games
    # are gone.
    assert [g["white"]["username"] for g in games[2:]] == [
        "NoIdentifier", "NoUuidLink", "NoUuidUrl", "BotBySuffix", "BotByTitle",
    ]


def test_recent_games_respects_oldest_first():
    client, _, _ = make_client(standard_routes())
    games = client.recent_games("beast-ash", newest_first=False)
    assert game_key(games[0]) == game_key(EDGE_GAMES["BotByTitle"])


def test_recent_games_month_limit():
    client, _, _ = make_client(standard_routes())
    games = client.recent_games("beast-ash", months=2)
    # Only September + August were consulted, so nothing from July leaked in.
    assert all(game_key(g).startswith("chesscom:") for g in games)
    assert len(games) == 2


def test_recent_games_max_games():
    client, _, _ = make_client(standard_routes())
    games = client.recent_games("beast-ash", max_games=3)
    assert len(games) == 3


def test_recent_games_time_class_filter():
    client, _, _ = make_client(standard_routes())
    rapid = client.recent_games("beast-ash", time_class="rapid")
    assert rapid and all(g["time_class"] == "rapid" for g in rapid)


def test_bots_are_included_by_default():
    client, _, _ = make_client(standard_routes())
    games = client.recent_games("beast-ash", include_from_position=True)
    assert any(is_bot(g) for g in games)


def test_bots_can_be_filtered_out():
    client, _, _ = make_client(standard_routes())
    games = client.recent_games("beast-ash", include_bots=False)
    assert not any(is_bot(g) for g in games)


def test_from_position_is_excluded_by_default():
    client, _, _ = make_client(standard_routes())
    games = client.recent_games("beast-ash", include_bots=False)
    whites = {g["white"]["username"] for g in games if g.get("white")}
    # The three from-position games are real, and the only reason they are gone.
    assert whites.isdisjoint({"FromPosByType", "FromPosBySetup", "FromPosByPgn"})
    assert "NoUuidUrl" in whites


def test_from_position_can_be_included():
    client, _, _ = make_client(standard_routes())
    games = client.recent_games("beast-ash", include_from_position=True)
    assert any(is_from_position(g) for g in games)


def test_games_without_pgn_are_always_dropped():
    client, _, _ = make_client(standard_routes())
    games = client.recent_games("beast-ash", include_from_position=True)
    assert all(g.get("pgn") for g in games)


def test_polite_pause_between_months_but_not_after_the_last():
    client, _, sleeper = make_client(standard_routes(), pause=0.4)
    client.recent_games("beast-ash", months=2)
    # Two months consulted -> exactly one pause, after the first.
    assert sleeper.calls == [0.4]


# --- caching --------------------------------------------------------------


def test_cache_serves_a_second_client_without_touching_the_network(tmp_path):
    routes = standard_routes()
    first, _, _ = make_client(routes, cache_dir=tmp_path)
    first.recent_games("beast-ash")
    first.close()

    # A brand-new client, same cache dir: every request must come from disk.
    second, router, _ = make_client(routes, cache_dir=tmp_path)
    assert second.recent_games("beast-ash")
    assert router.hits == []


def test_cache_is_skipped_when_disabled():
    client, router, _ = make_client(standard_routes(), cache_dir=None)
    client.recent_games("beast-ash")
    client.recent_games("beast-ash")
    # Both runs went to the network: with no cache_dir there was nowhere to
    # store the first response.
    assert router.hits.count(ARCHIVES_URL) == 2


def test_cache_ttl_expires(tmp_path):
    clock = Clock()
    routes = standard_routes()
    client, _, _ = make_client(routes, cache_dir=tmp_path, clock=clock)
    client.month_games(SEP)

    clock.now += chesscom.MONTH_TTL + 1
    client, router, _ = make_client(routes, cache_dir=tmp_path, clock=clock)
    client.month_games(SEP)
    assert router.hits == [SEP], "an expired entry must be refetched"


def test_fresh_cache_entry_is_not_refetched(tmp_path):
    clock = Clock()
    routes = standard_routes()
    client, _, _ = make_client(routes, cache_dir=tmp_path, clock=clock)
    client.month_games(SEP)

    clock.now += 60  # well inside MONTH_TTL
    client, router, _ = make_client(routes, cache_dir=tmp_path, clock=clock)
    assert client.month_games(SEP)
    assert router.hits == []


def test_404_responses_are_not_cached(tmp_path):
    routes = standard_routes()
    del routes[AUG]  # August 404s
    client, _, _ = make_client(routes, cache_dir=tmp_path)
    assert client.month_games(AUG) == []

    # The 404 must not have been frozen into the cache.
    restored, _, _ = make_client({**standard_routes(), AUG: (200, LIVE_MONTH)}, cache_dir=tmp_path)
    assert restored.month_games(AUG) == LIVE_MONTH["games"]


def test_corrupt_cache_entry_is_a_cache_miss(tmp_path):
    routes = standard_routes()
    client, _, _ = make_client(routes, cache_dir=tmp_path)
    client.month_games(SEP)

    for path in tmp_path.glob("*.json"):
        path.write_text("{ this is not json", encoding="utf-8")

    client, router, _ = make_client(routes, cache_dir=tmp_path)
    assert client.month_games(SEP) == LIVE_MONTH["games"]
    assert router.hits == [SEP]


def test_cache_entry_is_written_atomically_and_named_by_url_hash(tmp_path):
    client, _, _ = make_client(standard_routes(), cache_dir=tmp_path)
    client.month_games(SEP)

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert list(tmp_path.glob("*.tmp")) == []  # no half-written leftovers
    stored = json.loads(files[0].read_text(encoding="utf-8"))
    assert stored["url"] == SEP
    assert stored["json"] == LIVE_MONTH
    assert "fetched_at" in stored


def test_non_json_response_raises_bad_response():
    def handler(request):
        return httpx.Response(200, text="<html>oops</html>")

    client = ChessComClient(transport=httpx.MockTransport(handler), now=Clock())
    with pytest.raises(chesscom.BadResponse):
        client.month_games(SEP)
