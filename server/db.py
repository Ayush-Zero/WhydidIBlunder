"""SQLite storage for Phase 2: connection, pragmas, schema and migrations.

Design notes
------------
**Schema first.** The API and the worker are thin readers/writers; everything
hard about this phase (cache poisoning, atomic job claims) is decided *here*, in
the table shapes.

**Why SQLite.** One node, no Redis, no Postgres. WAL mode means the API process
can read while the worker writes — the whole reason WAL is switched on rather
than being left at the default rollback journal.

**Migrations.** A single ``schema_migrations`` table records which numbered
migrations have been applied; :func:`init_db` applies the rest in order and is
safe to call on every process start. Each migration script uses only
``IF NOT EXISTS`` DDL, so an interrupted migration re-runs to completion instead
of leaving a half-built schema behind (SQLite DDL *is* transactional, but
``executescript`` commits before it runs, so we do not get atomicity for free —
idempotent DDL is the cheaper guarantee here).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "DEFAULT_DB_PATH",
    "JOB_STATUSES",
    "MIGRATIONS",
    "MOVE_COLUMNS",
    "SCHEMA_VERSION",
    "USER_COLORS",
    "connect",
    "default_db_path",
    "enable_wal",
    "find_active_job",
    "get_job",
    "get_report",
    "init_db",
    "insert_job",
    "list_moves",
    "primary_key",
    "save_report",
    "schema_version",
    "table_columns",
    "tables",
    "upsert_move",
    "utcnow",
]

DEFAULT_DB_PATH = os.path.join("data", "whydidiblunder.db")

#: Whose moves a report covers (``--me`` / the ``user_color`` column).
USER_COLORS = ("white", "black")

#: Job lifecycle. ``queued -> running -> done`` is the happy path; ``failed``
#: and ``cancelled`` are the two ways out that are *not* success. The reaper
#: (Phase 2 B4) moves ``running`` back to ``queued`` when a worker dies.
JOB_STATUSES = ("queued", "running", "done", "failed", "cancelled")


def utcnow() -> str:
    """ISO-8601 UTC timestamp.

    Stored as text so that ``ORDER BY created_at`` / ``WHERE started_at < ?``
    work lexicographically — every timestamp uses the same format and a single
    timezone, which is the only way string comparison is a safe sort.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: Numbered migrations, applied in order. Append; never edit an applied one.
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id           TEXT PRIMARY KEY,          -- uuid4; unguessable by design (C8)
            game_key     TEXT NOT NULL,             -- chess.com uuid, or url/link fallback (A3)
            username     TEXT NOT NULL,             -- whose perspective was requested
            user_color   TEXT NOT NULL CHECK (user_color IN ('white','black')),
            status       TEXT NOT NULL DEFAULT 'queued'
                         CHECK (status IN ('queued','running','done','failed','cancelled')),
            pgn          TEXT NOT NULL,             -- so a retry needs no refetch
            game_meta    TEXT NOT NULL DEFAULT '{}',-- JSON: url, players, result, time_class
            depth        INTEGER NOT NULL,
            multipv      INTEGER NOT NULL,
            progress_ply INTEGER NOT NULL DEFAULT 0,
            worker_id    TEXT,
            started_at   TEXT,
            finished_at  TEXT,
            attempts     INTEGER NOT NULL DEFAULT 0,
            error        TEXT,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );
        -- The claim query is "oldest queued job", so this index is the hot path.
        CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_jobs_game_key ON jobs(game_key);

        -- The shared position cache. fen_key is the *normalised FEN* only;
        -- engine_version is a key column (not baked into fen_key) so that a
        -- Stockfish bump simply stops matching, which is what stops a new
        -- build from silently serving an old build's evals.
        --
        -- PK column order is (fen_key, engine_version, depth, multipv) rather
        -- than the plan's (fen_key, depth, multipv, engine_version) because the
        -- read path is "deepest row at or above the requested depth for this
        -- fen+engine", and SQLite can only use an index prefix for that range
        -- scan when depth comes before multipv. Same columns, faster lookup.
        CREATE TABLE IF NOT EXISTS position_eval (
            fen_key        TEXT NOT NULL,
            engine_version TEXT NOT NULL,
            depth          INTEGER NOT NULL,
            multipv        INTEGER NOT NULL,
            fen_full       TEXT NOT NULL,           -- unnormalised, for debugging
            evals_json     TEXT NOT NULL,           -- the MultiPV lines, JSON
            created_at     TEXT NOT NULL,
            PRIMARY KEY (fen_key, engine_version, depth, multipv)
        );

        -- One row per analysed ply. (job_id, ply) rather than a report id so a
        -- worker can persist progress incrementally, which is what makes C3's
        -- "partial results" free.
        CREATE TABLE IF NOT EXISTS moves (
            job_id            TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            ply               INTEGER NOT NULL,
            move_number       INTEGER NOT NULL,
            side              TEXT NOT NULL CHECK (side IN ('white','black')),
            fen_before        TEXT NOT NULL,
            fen_after         TEXT NOT NULL,
            san               TEXT NOT NULL,
            best_san          TEXT NOT NULL,
            pv_san            TEXT NOT NULL DEFAULT '[]',  -- JSON array
            played_rank       INTEGER,
            eval_before_cp    INTEGER NOT NULL,
            eval_after_cp     INTEGER NOT NULL,
            eval_before_win_pct REAL NOT NULL,
            eval_after_win_pct  REAL NOT NULL,
            mate_before       INTEGER,
            mate_after        INTEGER,
            drop_win_pct      REAL NOT NULL,
            classification    TEXT NOT NULL
                              CHECK (classification IN ('ok','inaccuracy','mistake','blunder')),
            tags              TEXT NOT NULL DEFAULT '[]',  -- JSON array
            suppressed        INTEGER NOT NULL DEFAULT 0 CHECK (suppressed IN (0,1)),
            suppress_reason   TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (job_id, ply)
        );
        CREATE INDEX IF NOT EXISTS idx_moves_flagged ON moves(job_id, classification);

        -- One stored report per (game, colour). A composite key rather than a
        -- surrogate id: the API dedupes on exactly this pair, so making it the
        -- primary key makes "reuse an existing report" an ON CONFLICT, not a
        -- lookup-then-insert race.
        CREATE TABLE IF NOT EXISTS game_reports (
            game_key       TEXT NOT NULL,
            user_color     TEXT NOT NULL CHECK (user_color IN ('white','black')),
            job_id         TEXT REFERENCES jobs(id) ON DELETE SET NULL,
            schema_version INTEGER NOT NULL,
            report_json    TEXT NOT NULL,           -- the full frozen Phase 1 report
            engine_version TEXT NOT NULL,
            depth          INTEGER NOT NULL,
            multipv        INTEGER NOT NULL,
            moves_analysed INTEGER NOT NULL DEFAULT 0,
            accuracy       REAL,
            blunders       INTEGER NOT NULL DEFAULT 0,
            mistakes       INTEGER NOT NULL DEFAULT 0,
            inaccuracies   INTEGER NOT NULL DEFAULT 0,
            ok             INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL,
            PRIMARY KEY (game_key, user_color)
        );
        """,
    ),
    (
        2,
        """
        -- B2: the worker heartbeat. A separate column from ``started_at``
        -- because the reaper has to distinguish "slow" from "dead": a large
        -- analysis may legitimately run past the timeout, but a job that has
        -- stopped recording progress has not.
        ALTER TABLE jobs ADD COLUMN heartbeat_at TEXT;
        """,
    ),
    (
        3,
        """
        -- C2: at most one *active* job per (game, colour). The API checks before
        -- inserting, but a double-click is two requests racing between that check
        -- and the insert. A partial unique index makes the loser fail loudly
        -- instead of queueing the same analysis twice; the API then returns the
        -- winner. Finished jobs are outside the index, so a retry can queue.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_active_game
            ON jobs(game_key, user_color)
            WHERE status IN ('queued', 'running');
        """,
    ),
)

#: The schema version this build expects. Bump by appending to MIGRATIONS.
SCHEMA_VERSION = MIGRATIONS[-1][0]


def default_db_path() -> str:
    """Where the database lives, from config.

    ``DB_PATH`` wins if set (it is the simple, unambiguous form). Otherwise
    ``DATABASE_URL`` is parsed — ``sqlite:///./data/x.db`` yields the relative
    path ``./data/x.db``, and a fourth slash (``sqlite:////abs/x.db``) yields an
    absolute one. Any *other* scheme raises rather than silently creating a
    file called ``postgres://...``, which is the failure mode that would
    otherwise be discovered at deploy time.
    """
    explicit = os.getenv("DB_PATH")
    if explicit:
        return explicit

    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return DEFAULT_DB_PATH
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(
            f"DATABASE_URL must use the sqlite scheme, got {url!r} "
            f"(expected e.g. {prefix}./{DEFAULT_DB_PATH})"
        )
    return url[len(prefix):] or DEFAULT_DB_PATH


def connect(db_path: str | os.PathLike[str] | None = None, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Open a connection configured the way this app needs it.

    ``isolation_level=None`` means autocommit: we manage transactions explicitly
    (``BEGIN IMMEDIATE`` for the job claim, B2) instead of letting the sqlite3
    module's implicit deferred transactions decide for us — an implicit
    deferred transaction upgrades to a write lock only *after* reading, which is
    exactly the race the claim query must not have.

    ``timeout`` feeds both the C-level busy handler and ``PRAGMA busy_timeout``,
    so a second worker blocks politely instead of raising "database is locked".
    """
    path = db_path or default_db_path()
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    return conn


def enable_wal(conn: sqlite3.Connection) -> str:
    """Switch on WAL journalling and return the mode SQLite actually chose.

    Must be issued outside a transaction (SQLite's requirement, and we are in
    autocommit so that holds). Returns the reported mode lowercased so a test
    can assert ``"wal"`` rather than trusting the pragma to have worked — on a
    :memory: database SQLite silently falls back, which would otherwise go
    unnoticed until a concurrent-write bug appears in production.
    """
    row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
    return str(row[0]).lower()


def _migration_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )


def init_db(conn: sqlite3.Connection) -> int:
    """Create or upgrade the schema. Idempotent; safe on every process start.

    Returns the resulting schema version.
    """
    enable_wal(conn)
    _migration_table(conn)
    applied = {int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations")}
    for version, sql in MIGRATIONS:
        if version in applied:
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (version, utcnow()),
        )
    return schema_version(conn)


def schema_version(conn: sqlite3.Connection) -> int:
    """The highest applied migration version (0 on a database with none)."""
    _migration_table(conn)
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def tables(conn: sqlite3.Connection) -> set[str]:
    """User tables present in the database (handy for schema assertions)."""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in rows}


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str) -> str:
    """Validate an identifier before it is interpolated into a PRAGMA.

    PRAGMA takes no bound parameters, so the name has to be interpolated — and
    an interpolated string is exactly the shape of a SQL-injection bug. These
    helpers are only ever called with our own table names, but validating
    anyway means a future caller passing user input fails loudly here instead of
    silently opening the door.
    """
    if not _IDENTIFIER.match(name or ""):
        raise ValueError(f"not a safe SQL identifier: {name!r}")
    return name


def table_columns(conn: sqlite3.Connection, name: str) -> list[str]:
    """Column names of ``name``, in declaration order."""
    return [row[1] for row in conn.execute(f"PRAGMA table_info({_ident(name)})")]


def primary_key(conn: sqlite3.Connection, name: str) -> list[str]:
    """Primary-key columns of ``name`` in key order (``PRAGMA table_info``'s
    ``pk`` column is 1-based and gaps are possible, so sort rather than filter)."""
    rows = [row for row in conn.execute(f"PRAGMA table_info({_ident(name)})") if row[5]]
    return [row[1] for row in sorted(rows, key=lambda r: r[5])]


def insert_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    game_key: str,
    username: str,
    user_color: str,
    pgn: str,
    depth: int,
    multipv: int,
    game_meta: str = "{}",
) -> str:
    """Insert a ``queued`` job and return its id.

    Pure row plumbing — the atomic *claim* of a queued job is Phase 2 B2 and
    lives elsewhere on purpose. Kept here so tests and the API have one place
    that knows the column list.
    """
    if user_color not in USER_COLORS:
        raise ValueError(f"user_color must be one of {USER_COLORS}, got {user_color!r}")
    now = utcnow()
    conn.execute(
        """
        INSERT INTO jobs (id, game_key, username, user_color, status, pgn, game_meta,
                          depth, multipv, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
        """,
        (job_id, game_key, username, user_color, pgn, game_meta, depth, multipv, now, now),
    )
    return job_id


def get_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    """Fetch one job row, or ``None`` if it does not exist."""
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def find_active_job(
    conn: sqlite3.Connection, game_key: str, user_color: str
) -> sqlite3.Row | None:
    """The queued/running job for this game + colour, if there already is one.

    This is C2's dedupe: a second request for a game already in the queue must
    return the existing job rather than spend Stockfish time twice. The partial
    unique index from migration 3 makes the same guarantee under a race, where
    this lookup can legitimately see nothing and then lose the insert.
    """
    return conn.execute(
        """
        SELECT * FROM jobs
         WHERE game_key = ? AND user_color = ?
           AND status IN ('queued', 'running')
         ORDER BY created_at DESC, id DESC
         LIMIT 1
        """,
        (game_key, user_color),
    ).fetchone()


# --------------------------------------------------------------------------- #
# results: moves and reports
# --------------------------------------------------------------------------- #
#: The move columns a :class:`~analyzer.report` wire dict maps onto, in the order
#: ``insert_move`` expects them. One list rather than two literals so the column
#: names and the parameter order cannot drift apart.
MOVE_COLUMNS = (
    "job_id",
    "ply",
    "move_number",
    "side",
    "fen_before",
    "fen_after",
    "san",
    "best_san",
    "pv_san",
    "played_rank",
    "eval_before_cp",
    "eval_after_cp",
    "eval_before_win_pct",
    "eval_after_win_pct",
    "mate_before",
    "mate_after",
    "drop_win_pct",
    "classification",
    "tags",
    "suppressed",
    "suppress_reason",
)


def upsert_move(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    move: dict,
    fen_before: str,
    fen_after: str,
) -> None:
    """Persist one analysed ply.

    ``move`` is a :func:`analyzer.report.move_to_dict` shape; ``fen_before`` /
    ``fen_after`` are supplied by the caller because the wire dict deliberately
    omits FENs (they are storage detail, and would bloat the API response).

    ``INSERT … ON CONFLICT DO UPDATE`` rather than a plain insert: a retried job
    re-runs the plies it had already written, and re-running must be idempotent
    instead of a primary-key violation. ``moves`` is keyed ``(job_id, ply)``, so
    ``ON CONFLICT`` targets exactly the retry case.
    """
    values = (
        job_id,
        move["ply"],
        move["move_number"],
        move["side"],
        fen_before,
        fen_after,
        move["san"],
        move["best_san"],
        json.dumps(list(move.get("pv_san") or [])),
        move.get("played_rank"),
        move["eval_before_cp"],
        move["eval_after_cp"],
        move["eval_before_win_pct"],
        move["eval_after_win_pct"],
        move.get("mate_before"),
        move.get("mate_after"),
        move["drop_win_pct"],
        move["classification"],
        json.dumps(list(move.get("tags") or [])),
        1 if move.get("suppressed") else 0,
        move.get("suppress_reason") or "",
    )
    columns = ", ".join(MOVE_COLUMNS)
    placeholders = ", ".join("?" * len(MOVE_COLUMNS))
    updates = ", ".join(
        f"{column} = excluded.{column}" for column in MOVE_COLUMNS if column != "job_id"
    )
    conn.execute(
        f"""
        INSERT INTO moves ({columns}) VALUES ({placeholders})
        ON CONFLICT(job_id, ply) DO UPDATE SET {updates}
        """,
        values,
    )


def list_moves(conn: sqlite3.Connection, job_id: str) -> list[sqlite3.Row]:
    """One job's moves in ply order."""
    return conn.execute(
        "SELECT * FROM moves WHERE job_id = ? ORDER BY ply", (job_id,)
    ).fetchall()


def save_report(
    conn: sqlite3.Connection,
    *,
    game_key: str,
    user_color: str,
    job_id: str | None,
    report: dict,
) -> None:
    """Store a finished report whole, plus its denormalised summary.

    Keyed ``(game_key, user_color)``: the same game analysed from both sides is
    two reports, and that pair is what the API dedupes on. The upsert makes
    "reuse or replace the report for this game+colour" a single statement rather
    than a lookup-then-insert race.
    """
    if user_color not in USER_COLORS:
        raise ValueError(f"user_color must be one of {USER_COLORS}, got {user_color!r}")
    summary = report.get("summary") or {}
    engine = report.get("engine") or {}
    now = utcnow()
    conn.execute(
        """
        INSERT INTO game_reports
            (game_key, user_color, job_id, schema_version, report_json,
             engine_version, depth, multipv, moves_analysed, accuracy,
             blunders, mistakes, inaccuracies, ok, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_key, user_color) DO UPDATE SET
            job_id         = excluded.job_id,
            schema_version = excluded.schema_version,
            report_json    = excluded.report_json,
            engine_version = excluded.engine_version,
            depth          = excluded.depth,
            multipv        = excluded.multipv,
            moves_analysed = excluded.moves_analysed,
            accuracy       = excluded.accuracy,
            blunders       = excluded.blunders,
            mistakes       = excluded.mistakes,
            inaccuracies   = excluded.inaccuracies,
            ok             = excluded.ok,
            updated_at     = excluded.updated_at
        """,
        (
            game_key,
            user_color,
            job_id,
            int(report.get("schema_version", 0)),
            json.dumps(report),
            str(engine.get("name", "")),
            int(engine.get("depth", 0)),
            int(engine.get("multipv", 0)),
            int(summary.get("moves_analysed", 0)),
            summary.get("accuracy"),
            int(summary.get("blunders", 0)),
            int(summary.get("mistakes", 0)),
            int(summary.get("inaccuracies", 0)),
            int(summary.get("ok", 0)),
            now,
            now,
        ),
    )


def get_report(
    conn: sqlite3.Connection, game_key: str, user_color: str
) -> sqlite3.Row | None:
    """The stored report row for ``(game_key, user_color)``, or ``None``."""
    return conn.execute(
        "SELECT * FROM game_reports WHERE game_key = ? AND user_color = ?",
        (game_key, user_color),
    ).fetchone()
