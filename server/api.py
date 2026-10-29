"""Phase 2 C1–C6: the game list, analysis, polling, cancel, report, and explain.

``GET /api/player/{username}/games`` is a thin, normalised view over
:mod:`server.chesscom`. It exists so the browser never talks to Chess.com
directly: the response shape, the bot/from-position policy, and the caching all
live on the server where they can be tested.

``POST /api/analyze`` turns one of those games into a queued job. It owns the
dedupe rules — a stored report wins over a queued job, an active job wins over a
new one — because those are API policy, not queue mechanics.

``GET /api/jobs/{job_id}`` is the read side of that queue. It returns the
persisted prefix of the analysis, which is what makes a long job observable
without pretending the unfinished part exists.

``POST /api/jobs/{job_id}/cancel`` is the write side: it flips the status row,
and the worker notices between plies. The API never talks to the engine
process directly; the database is the only channel.

``GET /api/games/{game_key}/report`` serves the frozen Phase 1 report that the
worker stored. It is a pure read: no engine, no Chess.com fetch, no reshaping.

``POST /api/explain`` is a Phase 2 stub. It accepts the Phase 5 LLM input shape
and returns a placeholder; no provider keys, prompts or LLM calls live here yet.

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

from server import db, queue
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
    "CancelResponse",
    "GameList",
    "GameSummary",
    "JobMove",
    "JobStatus",
    "PlayerSummary",
]

#: Chess.com usernames are letters, digits, ``_`` and ``-``. Constraining the
#: path parameter keeps a crafted name from becoming a different upstream URL.
USERNAME_PATTERN = r"^[A-Za-z0-9_-]{1,50}$"

#: The time classes Chess.com publishes. Anything else is a client mistake.
TimeClass = Literal["bullet", "blitz", "rapid", "daily"]

#: The job lifecycle, mirroring ``server.db.JOB_STATUSES``. Spelled as a
#: ``Literal`` so the generated schema names the values instead of saying
#: "string".
JobStatusValue = Literal["queued", "running", "done", "failed", "cancelled"]

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


# --- C3: poll a job -------------------------------------------------------


class JobMove(BaseModel):
    """One persisted ply, in the same wire shape as a report move.

    The storage row also carries ``fen_before``/``fen_after``; they are
    deliberately omitted here because they are storage detail and would bloat a
    response that is polled repeatedly.
    """

    ply: int
    move_number: int
    side: Literal["white", "black"]
    san: str
    best_san: str
    pv_san: list[str]
    eval_before_cp: int
    eval_after_cp: int
    eval_before_win_pct: float
    eval_after_win_pct: float
    mate_before: int | None = None
    mate_after: int | None = None
    played_rank: int | None = None
    drop_win_pct: float
    classification: Literal["ok", "inaccuracy", "mistake", "blunder"]
    tags: list[str]
    suppressed: bool
    suppress_reason: str


class JobStatus(BaseModel):
    """A job's state plus the part of its analysis that actually exists."""

    job_id: str
    status: JobStatusValue
    game_key: str
    user_color: Literal["white", "black"]
    #: Number of plies whose results are available. At least ``len(moves)``:
    #: a crash can persist a move before the progress counter advances, so the
    #: API reports the truthful prefix rather than the stale checkpoint.
    progress_ply: int
    moves: list[JobMove]
    #: The worker's failure or retry reason; ``None`` when there is nothing to
    #: say. A requeued job can carry the reaper's explanation while it waits.
    error: str | None = None


def _job_move(row: sqlite3.Row) -> JobMove:
    """Map a ``moves`` row onto the public move shape."""
    return JobMove(
        ply=row["ply"],
        move_number=row["move_number"],
        side=row["side"],
        san=row["san"],
        best_san=row["best_san"],
        pv_san=json.loads(row["pv_san"] or "[]"),
        eval_before_cp=row["eval_before_cp"],
        eval_after_cp=row["eval_after_cp"],
        eval_before_win_pct=row["eval_before_win_pct"],
        eval_after_win_pct=row["eval_after_win_pct"],
        mate_before=row["mate_before"],
        mate_after=row["mate_after"],
        played_rank=row["played_rank"],
        drop_win_pct=row["drop_win_pct"],
        classification=row["classification"],
        tags=json.loads(row["tags"] or "[]"),
        suppressed=bool(row["suppressed"]),
        suppress_reason=row["suppress_reason"],
    )


@api_router.get(
    "/jobs/{job_id}",
    response_model=JobStatus,
    summary="Poll a job's status and partial results",
)
def job_status(
    request: Request,
    job_id: str = Path(
        ...,
        min_length=1,
        max_length=64,
        description="The job_id returned by POST /api/analyze.",
    ),
) -> JobStatus:
    """Return one job's state and the plies persisted so far.

    This is deliberately a read-only view. Polling must not advance, retry or
    otherwise touch the job: a status check that changes state is impossible to
    reason about, and a browser polling every second would become a second
    scheduler.

    Partial results survive ``failed`` and ``cancelled`` jobs. They are what the
    worker actually wrote, which is more useful than pretending the analysis
    does not exist because it did not finish.
    """
    connection = db.connect(request.app.state.db_path)
    try:
        try:
            job = db.get_job(connection, job_id)
            if job is None:
                raise HTTPException(
                    status_code=404, detail=f"job {job_id!r} not found"
                )
            moves = db.list_moves(connection, job_id)
        except sqlite3.OperationalError as exc:
            # A fresh deployment can be polled before any worker has migrated
            # the file. That is "not ready", not a bug in this endpoint.
            raise HTTPException(
                status_code=503, detail="database is not ready"
            ) from exc
    finally:
        connection.close()

    return JobStatus(
        job_id=job["id"],
        status=job["status"],
        game_key=job["game_key"],
        user_color=job["user_color"],
        progress_ply=max(int(job["progress_ply"]), len(moves)),
        moves=[_job_move(row) for row in moves],
        error=job["error"],
    )


# --- C4: cancel a job -----------------------------------------------------


class CancelResponse(BaseModel):
    """The outcome of a cancel request.

    ``cancelled`` means *this call* moved the job to ``cancelled``. A job that
    was already terminal is returned with its current status and ``cancelled:
    false``, so a double-click is harmless.
    """

    job_id: str
    status: JobStatusValue
    cancelled: bool


@api_router.post(
    "/jobs/{job_id}/cancel",
    response_model=CancelResponse,
    summary="Cancel a queued or running job",
)
def cancel_job(
    request: Request,
    job_id: str = Path(
        ...,
        min_length=1,
        max_length=64,
        description="The job_id returned by POST /api/analyze.",
    ),
) -> CancelResponse:
    """Ask a job to stop, cooperatively.

    The status is written immediately, even for a ``running`` job: the worker
    polls the database between plies, so the row is the cancel channel. The
    worker may still be finishing the ply it had already started; this endpoint
    returns the requested state, not proof that the engine process has exited.

    Only ``queued`` and ``running`` jobs can be cancelled. A terminal job is
    returned as-is with ``cancelled: false``, which makes a double-click or a
    late request harmless.
    """
    connection = db.connect(request.app.state.db_path)
    try:
        try:
            changed = queue.request_cancel(connection, job_id)
            job = db.get_job(connection, job_id)
        except sqlite3.OperationalError as exc:
            raise HTTPException(
                status_code=503, detail="database is not ready"
            ) from exc
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    finally:
        connection.close()

    return CancelResponse(
        job_id=job_id,
        status=job["status"],
        cancelled=bool(changed and job["status"] == "cancelled"),
    )


# --- C5: fetch a stored report -------------------------------------------


@api_router.get(
    "/games/{game_key:path}/report",
    summary="Fetch the stored analysis report for a game",
    response_class=Response,
    responses={
        200: {
            "description": "The frozen Phase 1 report, exactly as the worker stored it.",
            "content": {"application/json": {"schema": {"type": "object"}}},
        },
        404: {"description": "No report for this game and colour."},
    },
)
def get_game_report(
    request: Request,
    game_key: str = Path(
        ...,
        min_length=1,
        max_length=2048,
        description="Opaque key from C1; may contain ':' and '/'.",
    ),
    user_color: Literal["white", "black"] = Query(
        ...,
        description="Which side the report covers; reports are stored per colour.",
    ),
) -> Response:
    """Return the stored report for ``(game_key, user_color)`` verbatim.

    The body is the frozen Phase 1 report (``docs/analyzer-schema.md``), not a
    re-serialised approximation: the worker's ``report_json`` is served as-is so
    the API cannot drift from the schema by adding or dropping fields.

    ``user_color`` is required because the same game can be analysed from both
    sides. ``(game_key, user_color)`` is the storage key, and a default would
    silently return the other player's report.

    The key may contain ``:`` and ``/`` (URL-derived keys), so the route uses a
    path converter rather than a single-segment parameter. Clients should
    URL-encode reserved characters such as ``?``, ``#`` and ``%``.
    """
    connection = db.connect(request.app.state.db_path)
    try:
        try:
            row = db.get_report(connection, game_key, user_color)
            body = None if row is None else row["report_json"]
        except sqlite3.OperationalError as exc:
            # Read-only, like C3/C4: a fresh deployment has no schema yet.
            raise HTTPException(
                status_code=503, detail="database is not ready"
            ) from exc
    finally:
        connection.close()

    if body is None:
        raise HTTPException(
            status_code=404,
            detail=f"no report for game {game_key!r} as {user_color}",
        )
    return Response(content=body, media_type="application/json")


# --- C6: explain one move (stub) -----------------------------------------


class ExplainRequest(BaseModel):
    """The engine facts an explanation is grounded in.

    This is deliberately the Phase 5 LLM input shape (``fen``, ``played``,
    ``best``, ``pv``, ``tags``), so the frontend can wire the call now and the
    backend can be filled in without changing the request. No PGN and no
    user-controlled prose: the LLM must explain engine facts, not invent them.
    """

    model_config = ConfigDict(extra="forbid")

    fen: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Position before the move, as FEN.",
    )
    played: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="The move that was played, in SAN.",
    )
    best: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="The engine's top move, in SAN.",
    )
    pv: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="The engine's best line, in SAN.",
    )
    tags: list[str] = Field(
        default_factory=list,
        max_length=16,
        description="Rule tags from the report (e.g. 'fork', 'pin').",
    )


class ExplainResponse(BaseModel):
    """A placeholder until Phase 5; ``stub`` lets the UI label it honestly."""

    explanation: str
    stub: bool = True


@api_router.post(
    "/explain",
    response_model=ExplainResponse,
    summary="Explain one move (placeholder until Phase 5)",
)
def explain_move(payload: ExplainRequest) -> ExplainResponse:
    """Return a fixed placeholder, not an explanation.

    Phase 2 only needs the route to exist so the frontend can call it. Building
    the LLM layer here would pull provider keys, prompts and cost controls into a
    milestone that is explicitly about the API surface. The request shape is the
    Phase 5 one so the frontend does not have to change later.

    The stub is deliberately **not** an echo of the request: user-controlled
    strings should not be reflected into the UI, and a fixed message cannot be
    mistaken for a real explanation.
    """
    return ExplainResponse(
        explanation=(
            "Explanations are not available yet. This is a placeholder so the "
            "frontend can wire the call; the engine's evaluation and best line "
            "are already in the game report."
        ),
        stub=True,
    )
