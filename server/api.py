"""Phase 2 C1–C2: the game list and the analysis request.

``GET /api/player/{username}/games`` is a thin, normalised view over
:mod:`server.chesscom`. It exists so the browser never talks to Chess.com
directly: the response shape, the bot/from-position policy, and the caching all
live on the server where they can be tested.

``POST /api/analyze`` turns one of those games into a queued job. It owns the
dedupe rules — a stored report wins over a queued job, an active job wins over a
new one — because those are API policy, not queue mechanics.

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

import json
import sqlite3
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from server import db
from server.chesscom import (
    ChessComClient,
    ChessComError,
    PlayerNotFound,
    game_key,
    is_bot,
    is_from_position,
)

__all__ = [
    "api_router",
    "AnalyzeRequest",
    "AnalyzeResponse",
    "GameList",
    "GameSummary",
    "PlayerSummary",
]

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


# --- C2: request an analysis ---------------------------------------------


class AnalyzeRequest(BaseModel):
    """The body of ``POST /api/analyze``.

    ``extra="forbid"`` on purpose: a typo like ``game_uuid`` should be a 422,
    not a silently ignored field that queues the wrong thing.
    """

    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        ...,
        pattern=USERNAME_PATTERN,
        description="Chess.com username (case-insensitive).",
    )
    game_key: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Opaque key from the game list; not parsed.",
    )
    months: int = Field(
        3,
        ge=1,
        le=120,
        description="How many archives to search for the game; must cover the list's range.",
    )


class AnalyzeResponse(BaseModel):
    """What the caller polls with, and whether this call did any new work."""

    #: ``None`` only in the defensive case where a stored report outlived its
    #: job row (``game_reports.job_id`` is ``ON DELETE SET NULL``). The report
    #: is still fetchable by ``(game_key, user_color)`` in C5.
    job_id: str | None
    status: Literal["queued", "running", "done"]
    reused: bool
    game_key: str
    user_color: Literal["white", "black"]


def _game_meta(game: dict) -> str:
    """The denormalised game summary stored on the job for the UI."""
    return json.dumps(
        {
            "url": game.get("url") or game.get("link"),
            "white": (game.get("white") or {}).get("username"),
            "black": (game.get("black") or {}).get("username"),
            "result": _result(game),
            "time_class": game.get("time_class"),
            "time_control": game.get("time_control"),
            "rated": bool(game.get("rated")),
            "rules": game.get("rules"),
            "end_time": game.get("end_time"),
        },
        separators=(",", ":"),
    )


def _reused(
    job_id: str | None,
    status: str,
    payload: AnalyzeRequest,
    color: str,
) -> AnalyzeResponse:
    return AnalyzeResponse(
        job_id=job_id,
        status=status,
        reused=True,
        game_key=payload.game_key,
        user_color=color,
    )


@api_router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    status_code=202,
    summary="Queue an analysis for one game",
)
def request_analysis(
    payload: AnalyzeRequest,
    request: Request,
    response: Response,
) -> AnalyzeResponse:
    """Queue an analysis, or return the job/report that already covers it.

    The precedence is deliberate:

    1. **A stored report wins.** Re-analysing a game we already have an answer
       for spends Stockfish time to produce the same JSON.
    2. **An active job wins.** A double-click must not queue the same game twice;
       the partial unique index (migration 3) makes that true under a race too.
    3. **Otherwise a new queued job.**

    Depth and MultiPV come from server config, not the request: letting a caller
    choose ``depth=40`` would be a trivial denial-of-service.
    """
    try:
        game = _client(request).find_game(
            payload.username, payload.game_key, months=payload.months
        )
    except PlayerNotFound as exc:
        raise HTTPException(
            status_code=404, detail=f"no chess.com player named {payload.username!r}"
        ) from exc
    except ChessComError as exc:
        raise HTTPException(
            status_code=502, detail=f"chess.com request failed: {exc}"
        ) from exc

    if game is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"game {payload.game_key!r} was not found in the last "
                f"{payload.months} month(s) for {payload.username!r}"
            ),
        )
    if not game.get("pgn"):
        raise HTTPException(
            status_code=422, detail="this game has no PGN to analyse"
        )
    if is_from_position(game):
        raise HTTPException(
            status_code=422,
            detail=(
                "games that start from a custom position cannot be analysed: "
                "they have no ply 1, which corrupts the phase labels"
            ),
        )

    color = _user_color(game, payload.username)
    if color is None:
        raise HTTPException(
            status_code=422,
            detail=f"{payload.username!r} did not play this game",
        )

    connection = db.connect(request.app.state.db_path)
    try:
        # Idempotent, and makes the API safe to start before a worker has ever
        # touched the file. Relative to an analysis it costs nothing.
        db.init_db(connection)

        stored = db.get_report(connection, payload.game_key, color)
        if stored is not None:
            response.status_code = 200
            return _reused(stored["job_id"], "done", payload, color)

        active = db.find_active_job(connection, payload.game_key, color)
        if active is not None:
            response.status_code = 200
            return _reused(active["id"], active["status"], payload, color)

        from analyzer import config as analyzer_config

        job_id = str(uuid.uuid4())
        try:
            db.insert_job(
                connection,
                job_id=job_id,
                game_key=payload.game_key,
                username=payload.username,
                user_color=color,
                pgn=game["pgn"],
                depth=analyzer_config.analysis_depth(),
                multipv=analyzer_config.analysis_multipv(),
                game_meta=_game_meta(game),
            )
        except sqlite3.IntegrityError:
            # We lost the race to another request between the lookup and the
            # insert. The winner is the job the caller wanted anyway.
            active = db.find_active_job(connection, payload.game_key, color)
            if active is None:  # pragma: no cover - the index guarantees one
                raise
            response.status_code = 200
            return _reused(active["id"], active["status"], payload, color)

        return AnalyzeResponse(
            job_id=job_id,
            status="queued",
            reused=False,
            game_key=payload.game_key,
            user_color=color,
        )
    finally:
        connection.close()
