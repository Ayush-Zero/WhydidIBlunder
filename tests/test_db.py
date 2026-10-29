"""A1 — SQLite schema, pragmas and migrations (offline: no engine, no network).

Storage is the foundation every later Phase-2 part stands on, so what is locked
here is the *shape*, not the behaviour of parts that haven't been built:

* a fresh database initialises to all four tables;
* WAL is genuinely on — asserted by reading the pragma back, not assumed;
* ``init_db`` is idempotent, including when two connections run it;
* the primary keys match the plan's queue/cache design, including the
  engine-versioned ``position_eval`` key that stops a Stockfish bump from
  silently serving an old build's evals;
* the foreign keys actually cascade.
"""

from __future__ import annotations

import sqlite3

import pytest

from server import db

EXPECTED_TABLES = {"jobs", "position_eval", "moves", "game_reports"}


@pytest.fixture()
def conn(tmp_path):
    """A migrated database on a real file (WAL needs a file, not :memory:)."""
    connection = db.connect(tmp_path / "test.db")
    db.init_db(connection)
    yield connection
    connection.close()


def _job(conn, job_id="job-1", game_key="chesscom:abc", user_color="white"):
    return db.insert_job(
        conn,
        job_id=job_id,
        game_key=game_key,
        username="BEAST-ASH",
        user_color=user_color,
        pgn='[White "A"]\n[Black "B"]\n\n1. e4 e5 *',
        depth=12,
        multipv=4,
    )


# --- initialisation -------------------------------------------------------


def test_fresh_db_creates_every_table(tmp_path):
    conn = db.connect(tmp_path / "fresh.db")
    try:
        assert db.tables(conn) == set()  # a bare file has nothing yet
        db.init_db(conn)
        assert db.tables(conn) == EXPECTED_TABLES | {"schema_migrations"}
    finally:
        conn.close()


def test_wal_is_enabled(conn):
    # enable_wal returns what SQLite actually chose; reading it back from a
    # fresh pragma catches the :memory: fallback that would otherwise hide.
    assert db.enable_wal(conn) == "wal"
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_wal_leaves_sidecar_files(tmp_path):
    path = tmp_path / "wal.db"
    conn = db.connect(path)
    try:
        db.init_db(conn)
        assert path.exists()
        assert (tmp_path / "wal.db-wal").exists()
    finally:
        conn.close()


def test_init_db_is_idempotent(tmp_path):
    path = tmp_path / "again.db"
    conn = db.connect(path)
    try:
        first = db.init_db(conn)
        objects_before = _schema_objects(conn)
        _job(conn, job_id="kept")

        assert db.init_db(conn) == first
        assert db.init_db(conn) == first
        assert _schema_objects(conn) == objects_before
        # The second pass must not have disturbed existing rows.
        assert db.get_job(conn, "kept") is not None
    finally:
        conn.close()


def test_schema_version_is_current(conn):
    assert db.schema_version(conn) == db.SCHEMA_VERSION


def test_migrations_apply_to_an_existing_database(tmp_path):
    """The upgrade path, not just the fresh-install path.

    A deployed v1 database must gain the B2 ``heartbeat_at`` column without
    losing its rows — the failure this guards is an ``ALTER TABLE`` that only
    ever runs on a brand-new file, which passes every fresh-install test and
    breaks the one machine that matters.
    """
    path = tmp_path / "v1.db"
    conn = db.connect(path)
    db.enable_wal(conn)
    db._migration_table(conn)
    conn.executescript(db.MIGRATIONS[0][1])  # a v1 schema, as deployed
    conn.execute(
        "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
        (db.utcnow(),),
    )
    db.insert_job(
        conn, job_id="old", game_key="k", username="u",
        user_color="white", pgn="p", depth=12, multipv=4,
    )
    assert "heartbeat_at" not in db.table_columns(conn, "jobs")
    conn.close()

    conn = db.connect(path)
    try:
        assert db.init_db(conn) == db.SCHEMA_VERSION
        assert "heartbeat_at" in db.table_columns(conn, "jobs")
        # Migrations are recorded, and the pre-existing row survived intact.
        assert [r[0] for r in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )] == [1, *range(2, db.SCHEMA_VERSION + 1)]
        assert db.get_job(conn, "old")["status"] == "queued"
    finally:
        conn.close()


def test_two_connections_can_both_initialise(tmp_path):
    """A worker and the API racing on first boot must not collide."""
    path = tmp_path / "race.db"
    a, b = db.connect(path), db.connect(path)
    try:
        db.init_db(a)
        db.init_db(b)
        db.insert_job(
            a, job_id="from-a", game_key="k", username="u",
            user_color="white", pgn="p", depth=12, multipv=4,
        )
        db.insert_job(
            b, job_id="from-b", game_key="k", username="u",
            user_color="black", pgn="p", depth=12, multipv=4,
        )
        count = a.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert count == 2
    finally:
        a.close()
        b.close()


def test_only_one_active_job_per_game_and_colour(conn):
    """C2's dedupe is a schema invariant, not just an API check.

    A double-click can race between the API's lookup and its insert; the partial
    unique index is what makes the loser fail instead of queueing the same work
    twice. Finished jobs are outside the index, so a retry is allowed.
    """
    _job(conn, "first", "chesscom:g", "white")
    with pytest.raises(sqlite3.IntegrityError):
        _job(conn, "second", "chesscom:g", "white")

    # The other colour is a different report, so it is allowed.
    _job(conn, "other-colour", "chesscom:g", "black")

    # Once the first is finished, the same game can be queued again.
    conn.execute("UPDATE jobs SET status = 'done' WHERE id = 'first'")
    _job(conn, "retry", "chesscom:g", "white")


def test_find_active_job_ignores_finished_jobs(conn):
    _job(conn, "queued", "chesscom:g", "white")
    assert db.find_active_job(conn, "chesscom:g", "white")["id"] == "queued"

    conn.execute("UPDATE jobs SET status = 'failed' WHERE id = 'queued'")
    assert db.find_active_job(conn, "chesscom:g", "white") is None


# --- table shape ----------------------------------------------------------


def test_jobs_columns_and_defaults(conn):
    cols = db.table_columns(conn, "jobs")
    for expected in (
        "id", "game_key", "username", "user_color", "status", "pgn", "game_meta",
        "depth", "multipv", "progress_ply", "worker_id", "started_at",
        "finished_at", "attempts", "error", "created_at", "updated_at",
    ):
        assert expected in cols, f"jobs is missing {expected!r}"
    assert db.primary_key(conn, "jobs") == ["id"]


def test_position_eval_key_matches_plan(conn):
    """PRIMARY KEY(fen_key, engine_version, depth, multipv).

    engine_version is a key *column* rather than part of fen_key, so a new
    Stockfish build simply stops matching instead of colliding with old rows —
    that is the anti-poisoning property B1 depends on.
    """
    assert db.primary_key(conn, "position_eval") == [
        "fen_key", "engine_version", "depth", "multipv",
    ]
    cols = db.table_columns(conn, "position_eval")
    for expected in ("fen_full", "evals_json", "created_at"):
        assert expected in cols


def test_moves_key_and_columns(conn):
    """(job_id, ply) so a worker can persist progress incrementally."""
    assert db.primary_key(conn, "moves") == ["job_id", "ply"]
    cols = db.table_columns(conn, "moves")
    for expected in (
        "fen_before", "fen_after", "san", "best_san", "pv_san", "played_rank",
        "eval_before_cp", "eval_after_cp", "eval_before_win_pct",
        "eval_after_win_pct", "mate_before", "mate_after", "drop_win_pct",
        "classification", "tags", "suppressed", "suppress_reason",
    ):
        assert expected in cols, f"moves is missing {expected!r}"


def test_game_reports_key_is_game_plus_colour(conn):
    """One stored report per (game, colour) — C2's "reuse if already done" is
    then an ON CONFLICT rather than a lookup-then-insert race."""
    assert db.primary_key(conn, "game_reports") == ["game_key", "user_color"]
    cols = db.table_columns(conn, "game_reports")
    for expected in ("job_id", "schema_version", "report_json", "engine_version",
                     "depth", "multipv", "accuracy", "blunders", "mistakes",
                     "inaccuracies", "ok"):
        assert expected in cols


# --- pragmas and constraints ----------------------------------------------


def test_foreign_keys_are_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_busy_timeout_is_set(conn):
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0


def test_deleting_a_job_cascades_to_its_moves(conn):
    _job(conn)
    conn.execute(
        "INSERT INTO moves (job_id, ply, move_number, side, fen_before, fen_after,"
        " san, best_san, eval_before_cp, eval_after_cp, eval_before_win_pct,"
        " eval_after_win_pct, drop_win_pct, classification)"
        " VALUES ('job-1', 1, 1, 'white', 'b', 'a', 'e4', 'e5', 0, 0, 50.0, 50.0, 0.0, 'ok')"
    )
    assert conn.execute("SELECT COUNT(*) FROM moves").fetchone()[0] == 1

    conn.execute("DELETE FROM jobs WHERE id = 'job-1'")

    assert conn.execute("SELECT COUNT(*) FROM moves").fetchone()[0] == 0


def test_invalid_user_color_is_rejected(conn):
    with pytest.raises(ValueError):
        _job(conn, user_color="green")


def test_invalid_job_status_is_rejected(conn):
    _job(conn)
    with pytest.raises(Exception) as excinfo:
        conn.execute("UPDATE jobs SET status = 'on-fire' WHERE id = 'job-1'")
    assert "CHECK constraint" in str(excinfo.value)


def test_invalid_classification_is_rejected(conn):
    _job(conn)
    with pytest.raises(Exception) as excinfo:
        conn.execute(
            "INSERT INTO moves (job_id, ply, move_number, side, fen_before,"
            " fen_after, san, best_san, eval_before_cp, eval_after_cp,"
            " eval_before_win_pct, eval_after_win_pct, drop_win_pct, classification)"
            " VALUES ('job-1', 1, 1, 'white', 'b', 'a', 'e4', 'e5', 0, 0, 50.0,"
            " 50.0, 0.0, 'catastrophe')"
        )
    assert "CHECK constraint" in str(excinfo.value)


# --- helpers --------------------------------------------------------------


def test_get_job_returns_none_for_unknown(conn):
    assert db.get_job(conn, "nope") is None


def test_get_job_round_trips_inserted_row(conn):
    _job(conn, game_key="chesscom-url:https://example/game/1")
    row = db.get_job(conn, "job-1")
    assert row["game_key"] == "chesscom-url:https://example/game/1"
    assert row["status"] == "queued"
    assert row["depth"] == 12 and row["multipv"] == 4
    assert row["progress_ply"] == 0


def test_connect_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "deeper" / "app.db"
    conn = db.connect(path)
    try:
        db.init_db(conn)
        assert path.exists()
    finally:
        conn.close()


def test_table_helpers_reject_unsafe_identifiers(conn):
    for bad in ("jobs; DROP TABLE jobs", "moves--", "", "1jobs"):
        with pytest.raises(ValueError):
            db.table_columns(conn, bad)


# --- configuration --------------------------------------------------------


def test_default_db_path_prefers_db_path(monkeypatch):
    monkeypatch.setenv("DB_PATH", "C:/somewhere/else.db")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./ignored.db")
    assert db.default_db_path() == "C:/somewhere/else.db"


def test_default_db_path_parses_relative_sqlite_url(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./data/whydidiblunder.db")
    assert db.default_db_path() == "./data/whydidiblunder.db"


def test_default_db_path_parses_absolute_sqlite_url(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:////var/data/app.db")
    assert db.default_db_path() == "/var/data/app.db"


def test_default_db_path_falls_back_when_unset(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.default_db_path() == db.DEFAULT_DB_PATH


def test_default_db_path_rejects_other_schemes(monkeypatch):
    """A Postgres URL must fail loudly, not become a file named "postgres:..."."""
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@host/db")
    with pytest.raises(ValueError):
        db.default_db_path()


def _schema_objects(connection):
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
        )
    }
