"""Client for Chess.com's public (key-less) API.

This exists so the API process and the worker talk to Chess.com in exactly one
place, with the awkward parts already handled. The awkward parts, from
``docs/phase2_breakdown.md``:

* **Response caching.** Chess.com is public and rate-limited by goodwill, not by
  a contract. Archives and past months are immutable, so their responses are
  cached on disk and never re-fetched.
* **Empty months (404).** Chess.com answers 404 — not ``{"games": []}`` — for a
  month in which a player has no games. That is a normal answer, not an error.
* **Bots.** Bot games are real games you played, but their ratings are
  meaningless, so callers need a cheap way to filter them.
* **"From position" games.** Games that start from a custom FEN have no ply 1,
  which quietly breaks anything that assumes a game opened from the standard
  setup (notably the phase heuristic, which is ply-based). Excluded by default.
* **No ``uuid``.** Older games have no ``uuid``; the game key falls back to
  ``url``, then ``link``, then a digest of the players and clock.

Everything network-facing is injectable (``transport``, ``sleep``, ``now``), so
the tests below run over recorded JSON with no network at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

API_BASE = "https://api.chess.com/pub"
USER_AGENT = "WhydidIBlunder/0.1 (github.com/Ayush-Zero/WhydidIBlunder)"

#: ``CHESSCOM_CACHE_DIR`` env: where cached responses live. Unset (or empty)
#: disables caching entirely, which is what the tests want and what a one-off
#: CLI run probably wants too.
CACHE_DIR_ENV = "CHESSCOM_CACHE_DIR"

#: The archives list grows (and its newest entry mutates) as a player competes.
ARCHIVE_TTL = 600.0
#: A listed month is nearly immutable; only the *current* month still changes.
MONTH_TTL = 3600.0

#: How chess.com marks a bot account. Both signals are used because neither is
#: guaranteed on its own: the title is authoritative when present, and the
#: ``--bot`` suffix covers bot accounts that omit it.
BOT_TITLE = "bot"
BOT_SUFFIX = "--bot"

#: The first four FEN fields — the position itself, without the clocks. Used to
#: tell a standard-start game from one that starts mid-game.
_STANDARD_POSITION = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"


class ChessComError(RuntimeError):
    """Base class for every failure this client raises."""


class PlayerNotFound(ChessComError):
    """The player does not exist, or Chess.com has never seen them."""


class BadResponse(ChessComError):
    """Chess.com answered with something we cannot parse."""


@dataclass(frozen=True)
class CachedResponse:
    """One cached HTTP body, with the fetch time so a TTL can be applied."""

    url: str
    payload: Any
    fetched_at: float


# --- pure game inspection (no I/O, so trivially unit-testable) -------------


def cache_dir_from_env() -> str | None:
    """Read ``CHESSCOM_CACHE_DIR`` from config; empty or unset means "no cache"."""
    return os.getenv(CACHE_DIR_ENV, "").strip() or None


def _fen_position(fen: str) -> str:
    """The position half of a FEN: board, side, castling rights, en passant."""
    return " ".join(str(fen).split()[:4])


def is_bot(game: dict) -> bool:
    """True if either player is a chess.com bot account."""
    for side in ("white", "black"):
        player = game.get(side) or {}
        if str(player.get("user_title") or "").strip().lower() == BOT_TITLE:
            return True
        if str(player.get("username") or "").lower().endswith(BOT_SUFFIX):
            return True
    return False


def is_from_position(game: dict) -> bool:
    """True if the game starts from a custom position rather than move 1.

    Checked in order of authority: the API's own ``type`` field, then
    ``initial_setup``, then a ``[FEN ...]`` tag in the PGN. The last two are a
    fallback for games where ``type`` is null (which, as observed on live data,
    is the common case for ordinary games — so ``type`` alone is not enough).
    """
    if str(game.get("type") or "").strip().lower() == "fromposition":
        return True

    setup = game.get("initial_setup")
    if setup and _fen_position(setup) != _STANDARD_POSITION:
        return True

    pgn = str(game.get("pgn") or "")
    marker = '[FEN "'
    start = pgn.find(marker)
    if start != -1:
        end = pgn.find('"', start + len(marker))
        if end != -1:
            return _fen_position(pgn[start + len(marker):end]) != _STANDARD_POSITION
    return False


def game_key(game: dict) -> str:
    """A stable key for a game, preferring chess.com's own identifiers.

    Priority, as the plan requires: ``uuid`` → ``url`` → ``link`` → a digest of
    the players and clock. The prefixes make the provenance of any key visible
    in the database, which matters because the API's URLs are built from it.
    """
    uuid = game.get("uuid")
    if uuid:
        return f"chesscom:{uuid}"

    for field in ("url", "link"):
        value = game.get(field)
        if value:
            return f"chesscom-url:{value}"

    # Older daily games can arrive with none of the three. Derive something
    # stable rather than raising: a duplicate report is a worse outcome than a
    # slightly opaque key.
    players = "|".join(
        str((game.get(side) or {}).get("username") or "")
        for side in ("white", "black")
    )
    fingerprint = "|".join(
        [
            players,
            str(game.get("end_time") or game.get("end_time_iso") or ""),
            str(game.get("time_control") or ""),
        ]
    )
    return "derived:" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]


# --- the client -----------------------------------------------------------


class ChessComClient:
    """Fetches a player's games, caching responses on disk.

    Parameters
    ----------
    transport:
        An ``httpx`` transport. Tests pass ``httpx.MockTransport`` so no request
        ever leaves the machine.
    cache_dir:
        Directory for cached responses; ``None`` disables caching entirely.
    now / sleep:
        Injectable clock and sleeper, so TTL and politeness behaviour is testable
        without waiting.
    pause:
        Seconds to wait between month requests. Chess.com publishes no rate
        limits; being slow is the polite default, and it costs nothing because
        we cache.
    """

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        cache_dir: str | os.PathLike[str] | None = None,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        pause: float = 0.4,
        timeout: float = 30.0,
    ) -> None:
        self._transport = transport
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._now = now
        self._sleep = sleep
        self._pause = pause
        self._timeout = timeout
        self._client: httpx.Client | None = None

    # -- lifecycle -------------------------------------------------------

    def __enter__(self) -> ChessComClient:
        self.client  # noqa: B018 - force the connection open now, not mid-request
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                transport=self._transport,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
                timeout=self._timeout,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # -- raw fetching ----------------------------------------------------

    def get_json(self, url: str, *, ttl: float | None = None) -> Any:
        """GET a JSON document, consulting the cache first.

        Returns the parsed body, or ``None`` on a 404. A 404 is a *normal*
        answer — chess.com uses it for a month with no games — so it is neither
        raised nor cached: caching it would freeze a transient gap into the
        cache permanently.

        ``ttl=None`` means "immutable" (serve the cached copy forever);
        otherwise the cached copy is used only while younger than ``ttl``.
        """
        cached = self._read_cache(url)
        if cached is not None and (ttl is None or (self._now() - cached.fetched_at) < ttl):
            return cached.payload

        response = self.client.get(url)
        if response.status_code == 404:
            return None
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After", "?")
            raise ChessComError(f"chess.com rate limited us (Retry-After: {retry_after})")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ChessComError(f"chess.com returned {response.status_code} for {url}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise BadResponse(f"chess.com returned non-JSON for {url}") from exc

        self._write_cache(url, payload)
        return payload

    # -- on-disk cache ---------------------------------------------------

    def _cache_path(self, url: str) -> Path | None:
        if self._cache_dir is None:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self._cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> CachedResponse | None:
        path = self._cache_path(url)
        if path is None or not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return CachedResponse(
                url=raw["url"], payload=raw["json"], fetched_at=float(raw["fetched_at"])
            )
        except (OSError, ValueError, KeyError, TypeError):
            # A truncated or hand-edited cache entry is a cache miss, not a
            # crash: the next request simply refetches.
            return None

    def _write_cache(self, url: str, payload: Any) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        body = {"url": url, "json": payload, "fetched_at": self._now()}
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename: an interrupted write must never be readable as a
        # half-written cache entry.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    # -- public API ------------------------------------------------------

    def list_archives(self, username: str) -> list[str]:
        """The URLs of every month this player has games in, oldest first.

        Raises :class:`PlayerNotFound` if chess.com has never heard of the
        player. A player with an account but no games is *not* an error — the
        API returns an empty list, and so do we.
        """
        document = self.get_json(
            f"{API_BASE}/player/{username}/games/archives", ttl=ARCHIVE_TTL
        )
        if document is None:
            raise PlayerNotFound(f"no chess.com player named {username!r}")
        return [str(url) for url in (document.get("archives") or [])]

    def month_games(self, archive_url: str) -> list[dict]:
        """The games in one month's archive. A missing month is just no games."""
        document = self.get_json(archive_url, ttl=MONTH_TTL)
        if not document:
            return []
        return [game for game in (document.get("games") or []) if isinstance(game, dict)]

    def recent_games(
        self,
        username: str,
        *,
        months: int | None = None,
        max_games: int | None = None,
        time_class: str | None = None,
        include_bots: bool = True,
        include_from_position: bool = False,
        newest_first: bool = True,
    ) -> list[dict]:
        """Collect this player's recent games, newest first by default.

        Games without a PGN are always dropped — there is nothing to analyse.
        ``include_from_position`` defaults to ``False`` because such games have
        no ply 1, which silently corrupts the ply-based phase heuristic; callers
        that want them must say so explicitly.
        """
        archives = self.list_archives(username)
        if newest_first:
            archives = list(reversed(archives))
        if months is not None:
            archives = archives[:months]

        collected: list[dict] = []
        for index, url in enumerate(archives):
            games = self.month_games(url)
            if newest_first:
                games = list(reversed(games))
            for game in games:
                if not game.get("pgn"):
                    continue
                if time_class is not None and game.get("time_class") != time_class:
                    continue
                if not include_bots and is_bot(game):
                    continue
                if not include_from_position and is_from_position(game):
                    continue
                collected.append(game)
                if max_games is not None and len(collected) >= max_games:
                    return collected
            # Be polite between months, but not after the last one.
            if index < len(archives) - 1 and self._pause:
                self._sleep(self._pause)
        return collected
