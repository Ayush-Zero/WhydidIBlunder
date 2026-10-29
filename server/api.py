"""Phase 2 C1: the read-only game list.

``GET /api/player/{username}/games`` is a thin, normalised view over
:mod:`server.chesscom`. It exists so the browser never talks to Chess.com
directly: the response shape, the bot/from-position policy, and the caching all
live on the server where they can be tested.

Three decisions worth stating:

* **No PGN in the list.** A month's games are large, and the list is fetched on
  every page load. The PGN is needed only when analysis is requested (C2), which
  refetches it through the same disk cache. Keeping it out keeps the payload
  proportional to the number of games, not the number of moves.
* **``game_key`` is the identifier, not the raw ``uuid``.** Older games have no
  ``uuid`` (A3), so the key is the one field the client can always produce.
  Callers must treat it as opaque.
* **Upstream failures are 502, not 500.** A Chess.com outage or rate limit is a
  bad gateway, not a bug in this service; ``PlayerNotFound`` is the one upstream
  answer that is really a 404.

Everything is injectable through the app factory: tests pass a
``ChessComClient`` backed by recorded JSON, so these tests never touch the
network.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel

from server.chesscom import (
    ChessComClient,
    ChessComError,
    PlayerNotFound,
    game_key,
    is_bot,
    is_from_position,
)

__all__ = ["api_router", "GameList", "GameSummary", "PlayerSummary"]

#: Chess.com usernames are letters, digits, ``_`` and ``-``. Constraining the
#: path parameter keeps a crafted name from becoming a different upstream URL.
USERNAME_PATTERN = r"^[A-Za-z0-9_-]{1,50}$"

#: The time classes Chess.com publishes. Anything else is a client mistake.
TimeClass = Literal["bullet", "blitz", "rapid", "daily"]

api_router = APIRouter(prefix="/api", tags=["games"])


# --- the response contract ------------------------------------------------


class PlayerSummary(BaseModel):
    """One side of a game, as the browser needs it."""

    username: str
    rating: int | None = None
    result: str | None = None
    title: str | None = None


class GameSummary(BaseModel):
    """One normalised game. Deliberately excludes the PGN (see module docstring)."""

    game_key: str
    url: str | None = None
    white: PlayerSummary
    black: PlayerSummary
    result: str | None = None
    time_class: str | None = None
    time_control: str | None = None
    rated: bool = False
    rules: str | None = None
    end_time: int | None = None
    is_bot: bool
    from_position: bool
    #: Which side the requested username played. ``None`` only if the username
    #: does not appear in the game, which would mean an inconsistent upstream
    #: response rather than a normal case.
    user_color: Literal["white", "black"] | None = None


class GameList(BaseModel):
    username: str
    count: int
    games: list[GameSummary]


# --- normalisation --------------------------------------------------------


def _player(game: dict, side: str) -> PlayerSummary:
    player = game.get(side) or {}
    return PlayerSummary(
        username=str(player.get("username") or ""),
        rating=player.get("rating"),
        result=player.get("result"),
        title=player.get("user_title"),
    )


def _result(game: dict) -> str | None:
    """The game result from the two per-player results.

    Chess.com does not put ``1-0``/``0-1`` on the game object; it marks the
    winner's ``result`` as ``"win"`` and gives the loser a reason
    (``checkmated``, ``resigned``, …). An unfinished game is ``None`` rather
    than a guessed draw.
    """
    white = (game.get("white") or {}).get("result")
    black = (game.get("black") or {}).get("result")
    if white == "win":
        return "1-0"
    if black == "win":
        return "0-1"
    if white in (None, "*") or black in (None, "*"):
        return None
    return "1/2-1/2"


def _user_color(game: dict, username: str) -> Literal["white", "black"] | None:
    """Which side the requested player had, case-insensitively.

    Chess.com treats usernames case-insensitively and returns the canonical
    casing, so an exact comparison would fail for a user who typed ``beast-ash``.
    """
    wanted = username.lower()
    for side in ("white", "black"):
        if str((game.get(side) or {}).get("username") or "").lower() == wanted:
            return "white" if side == "white" else "black"
    return None


def _summary(game: dict, username: str) -> GameSummary:
    return GameSummary(
        game_key=game_key(game),
        url=game.get("url") or game.get("link"),
        white=_player(game, "white"),
        black=_player(game, "black"),
        result=_result(game),
        time_class=game.get("time_class"),
        time_control=game.get("time_control"),
        rated=bool(game.get("rated")),
        rules=game.get("rules"),
        end_time=game.get("end_time"),
        is_bot=is_bot(game),
        from_position=is_from_position(game),
        user_color=_user_color(game, username),
    )


# --- the endpoint ---------------------------------------------------------


def _client(request: Request) -> ChessComClient:
    """The one client the app owns (built by ``create_app``)."""
    return request.app.state.chesscom


@api_router.get(
    "/player/{username}/games",
    response_model=GameList,
    summary="List a player's recent games",
)
def player_games(
    request: Request,
    username: str = Path(
        ...,
        pattern=USERNAME_PATTERN,
        description="Chess.com username (case-insensitive).",
    ),
    months: int = Query(
        3,
        ge=1,
        le=120,
        description="How many recent monthly archives to consult.",
    ),
    max_games: int = Query(
        50,
        ge=1,
        le=500,
        description="Stop after this many games.",
    ),
    time_class: TimeClass | None = Query(
        None,
        description="Restrict to one time class; omit for all.",
    ),
    include_bots: bool = Query(
        True,
        description="Bot games are included by default; their ratings are meaningless.",
    ),
    include_from_position: bool = Query(
        False,
        description="Include games starting from a custom FEN (breaks phase labels).",
    ),
) -> GameList:
    """Return this player's recent games, newest first, normalised for the UI.

    Served through the client's on-disk cache, so a repeat call is a disk read
    rather than a second trip to Chess.com.
    """
    try:
        games = _client(request).recent_games(
            username,
            months=months,
            max_games=max_games,
            time_class=time_class,
            include_bots=include_bots,
            include_from_position=include_from_position,
            newest_first=True,
        )
    except PlayerNotFound as exc:
        raise HTTPException(
            status_code=404, detail=f"no chess.com player named {username!r}"
        ) from exc
    except ChessComError as exc:
        # A Chess.com outage or rate limit must not look like a bug in us.
        raise HTTPException(
            status_code=502, detail=f"chess.com request failed: {exc}"
        ) from exc

    return GameList(
        username=username,
        count=len(games),
        games=[_summary(game, username) for game in games],
    )
