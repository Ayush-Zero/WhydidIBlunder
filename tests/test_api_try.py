"""D1 — ``POST /api/games/{game_key}/try`` with a fake engine.

The fake engine makes the test deterministic and offline: it returns whatever
score the test asks for, and records every call. That lets us pin the grading
ladder without asserting Stockfish's opinion of any particular position.

The report is a real four-ply sequence (``1.e3 f6 2.Qh5+ g6``). At ply 4 Black
has exactly one legal move, which is how the forced-move branch is exercised
against an otherwise losing score.

The opt-in real-Stockfish plumbing test lives in ``test_live_try.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import chess
import chess.engine
import httpx
import pytest
from fastapi.testclient import TestClient

from analyzer.cache import MemoryPositionCache
from analyzer.engine import EngineTimeoutError
from analyzer.winpct import win_pct
from server import db
from server.app import create_app
from server.chesscom import ChessComClient
from server.ratelimit import RateLimiter

KEY = "chesscom:try-game"


@pytest.fixture(autouse=True)
def _default_thresholds(monkeypatch):
    """Pin the plan's defaults; the developer's .env must not change the test."""
    monkeypatch.setenv("THRESHOLD_INACCURACY", "10")
    monkeypatch.setenv("THRESHOLD_MISTAKE", "20")
    monkeypatch.setenv("THRESHOLD_BLUNDER", "30")
    monkeypatch.setenv("SUPPRESS_WIN_PTS", "0.2")


def _move(
    ply: int,
    san: str,
    side: str,
    *,
    best_san: str,
    before_cp: int,
    pv_san: list[str] | None = None,
) -> dict:
    return {
        "ply": ply,
        "move_number": (ply + 1) // 2,
        "side": side,
        "san": san,
        "best_san": best_san,
        "pv_san": pv_san or [best_san],
        "eval_before_cp": before_cp,
        "eval_after_cp": before_cp,
        "eval_before_win_pct": win_pct(before_cp),
        "eval_after_win_pct": win_pct(before_cp),
        "mate_before": None,
        "mate_after": None,
        "played_rank": None,
        "drop_win_pct": 0.0,
        "classification": "ok",
        "tags": [],
        "suppressed": False,
        "suppress_reason": "",
    }


#: A legal report whose fourth ply is Black's only legal reply (g6).
REPORT = {
    "schema_version": 1,
    "engine": {"name": "Fake Engine 1", "depth": 12, "multipv": 4},
    "game": {
        "white": "White",
        "black": "Black",
        "result": "*",
        "date": "",
        "event": "",
        "eco": "",
        "link": "",
        "user_color": "white",
    },
    "moves": [
        _move(
            1,
            "e3",
            "white",
            best_san="e4",
            before_cp=0,
            pv_san=["e4", "e5", "Nf3"],
        ),
        _move(2, "f6", "black", best_san="e5", before_cp=10),
        _move(3, "Qh5+", "white", best_san="Qh5+", before_cp=200),
        _move(4, "g6", "black", best_san="g6", before_cp=-100),
    ],
    "summary": {
        "user_color": "white",
        "moves_analysed": 2,
        "accuracy": 80.0,
        "blunders": 0,
        "mistakes": 0,
        "inaccuracies": 0,
        "ok": 2,
    },
}


class FakeEngine:
    """A Stockfish stand-in with a controllable, board-relative score."""

    def __init__(self, score_for=None) -> None:
        self.name = "Fake Engine 1"
        self._score_for = score_for or (lambda _board: 0)
        self.calls: list[tuple[str, int, int, float | None]] = []
        self.closed = 0

    def analyse(self, board, depth, multipv, timeout=None):
        self.calls.append((board.fen(), depth, multipv, timeout))
        score = chess.engine.PovScore(
            chess.engine.Cp(int(self._score_for(board))), board.turn
        )
        first = next(iter(board.legal_moves), None)
        pv = [first] if first is not None else []
        return [{"score": score, "pv": pv} for _ in range(multipv)]

    def close(self) -> None:
        self.closed += 1


class TimedOutEngine(FakeEngine):
    """A fake engine that reports the hard-timeout path."""

    def analyse(self, board, depth, multipv, timeout=None):
        self.calls.append((board.fen(), depth, multipv, timeout))
        raise EngineTimeoutError("simulated timeout")


def _chesscom() -> ChessComClient:
    return ChessComClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
        cache_dir=None,
        pause=0.0,
    )


@contextmanager
def running_app(
    tmp_path: Path,
    *,
    engine: FakeEngine | None = None,
    cache: MemoryPositionCache | None = None,
    try_limiter: RateLimiter | None = None,
):
    app = create_app(
        configure_logging_now=False,
        chesscom_client=_chesscom(),
        db_path=str(tmp_path / "try.db"),
        try_engine_factory=(lambda: engine) if engine is not None else None,
        try_cache_factory=(lambda _conn: cache) if cache is not None else None,
        try_rate_limiter=try_limiter,
    )
    with TestClient(app) as client:
        yield client, tmp_path / "try.db"


def _seed(db_path: Path, report: dict | None = None) -> None:
    connection = db.connect(db_path)
    try:
        db.init_db(connection)
        db.save_report(
            connection,
            game_key=KEY,
            user_color="white",
            job_id=None,
            report=REPORT if report is None else report,
        )
    finally:
        connection.close()


def _try(
    client: TestClient,
    *,
    ply: int = 1,
    move_san: str = "d4",
    user_color: str = "white",
    game_key: str = KEY,
):
    return client.post(
        f"/api/games/{game_key}/try",
        json={"user_color": user_color, "ply": ply, "move_san": move_san},
    )


# --------------------------------------------------------------------------- #
# the acceptance ladder
# --------------------------------------------------------------------------- #


def test_the_exact_best_move_is_accepted_and_returns_the_report_line(tmp_path):
    engine = FakeEngine(lambda _board: 900)
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path)
        response = _try(client, ply=1, move_san="e4")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["legal"] is True
    assert body["accepted"] is True
    assert body["classification"] == "ok"
    assert body["best_san"] == "e4"
    assert body["pv_san"] == ["e4", "e5", "Nf3"]
    assert body["threshold_win_pct"] == 10.0
    assert len(engine.calls) == 1
    _fen, depth, multipv, timeout = engine.calls[0]
    assert (depth, multipv) == (12, 4)
    assert timeout is not None, "try-again must have a hard timeout"


def test_a_forced_move_passes_even_when_the_after_eval_is_terrible(tmp_path):
    # ``g6`` is Black's only legal reply after 2.Qh5+. The fake score makes the
    # move look losing; precedence #1 (forced) must still accept it.
    engine = FakeEngine(lambda _board: 1_000)
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path)
        response = _try(client, ply=4, move_san="g6", user_color="white")

    # The report is stored for White, so ply 4 (Black) is the wrong side.
    assert response.status_code == 422
    assert "black" in response.json()["detail"]

    connection = db.connect(tmp_path / "try.db")
    try:
        db.save_report(
            connection,
            game_key=KEY,
            user_color="black",
            job_id=None,
            report={**REPORT, "game": {**REPORT["game"], "user_color": "black"}},
        )
    finally:
        connection.close()

    with running_app(tmp_path, engine=engine) as (client, db_path):
        response = _try(client, ply=4, move_san="g6", user_color="black")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] is True
    assert body["classification"] == "ok"
    assert body["reason"] == "the position has only one legal move"
    assert body["drop_win_pct"] > 30, "the fake score is genuinely terrible"


def test_the_report_before_win_pct_is_the_baseline_not_a_recomputation(tmp_path):
    # If the endpoint recomputed from eval_before_cp (0cp -> 50%), this d4 with a
    # 0cp after-eval would be a 0-point drop and pass. The stored baseline says
    # 60%, so the drop is 10 points and crosses the inaccuracy threshold.
    report = {
        **REPORT,
        "moves": [
            {**REPORT["moves"][0], "eval_before_win_pct": 60.0},
            *REPORT["moves"][1:],
        ],
    }
    engine = FakeEngine(lambda _board: 0)
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path, report=report)
        response = _try(client, ply=1, move_san="d4")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["classification"] == "inaccuracy"
    assert body["accepted"] is False
    assert body["drop_win_pct"] == 10.0


def test_a_non_best_move_within_the_suppression_gap_passes(tmp_path):
    # After d4 the engine is 1cp better for Black (from Black's POV), so White's
    # after-eval is -1: a drop far below 0.2 win%-points. d4 is not the report's
    # best move, which is the point.
    engine = FakeEngine(lambda _board: 1)
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path)
        response = _try(client, ply=1, move_san="d4")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] is True
    assert body["classification"] == "ok"
    assert body["best_san"] == "e4"
    assert 0 <= body["drop_win_pct"] <= 0.2
    assert "within" in body["reason"]


def test_a_transposition_like_second_best_passes_when_its_eval_matches(tmp_path):
    """Different move, same resulting evaluation: acceptance is about the drop.

    This is the reason grading against ``best_san`` alone would be wrong.
    """
    engine = FakeEngine(lambda _board: 0)
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path)
        response = _try(client, ply=1, move_san="Nf3")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] is True
    assert body["classification"] == "ok"
    assert body["best_san"] == "e4"
    assert body["drop_win_pct"] == 0.0


@pytest.mark.parametrize(
    ("engine_cp", "classification", "accepted"),
    [
        (110, "ok", True),  # 9.99-point drop: still below inaccuracy
        (111, "inaccuracy", False),  # crosses 10
        (231, "mistake", False),  # crosses 20
        (400, "blunder", False),  # crosses 30
    ],
)
def test_the_report_thresholds_decide_each_label(
    tmp_path, engine_cp, classification, accepted
):
    # The score is anchored to the side to move *after* d4 (Black), so a positive
    # score is good for Black and a negative after-eval for White.
    engine = FakeEngine(lambda _board: engine_cp)
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path)
        response = _try(client, ply=1, move_san="d4")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["classification"] == classification
    assert body["accepted"] is accepted
    assert body["threshold_win_pct"] == 10.0
    if not accepted:
        assert "worse than the position before the move" in body["reason"]


# --------------------------------------------------------------------------- #
# validation and errors
# --------------------------------------------------------------------------- #


def test_an_illegal_candidate_is_422_before_the_engine_runs(tmp_path):
    engine = FakeEngine()
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path)
        response = _try(client, ply=1, move_san="Qh9")

    assert response.status_code == 422
    assert "not a legal move" in response.json()["detail"]
    assert engine.calls == []


def test_a_bad_ply_is_422(tmp_path):
    engine = FakeEngine()
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path)
        response = _try(client, ply=99, move_san="e4")

    assert response.status_code == 422
    assert "outside this report" in response.json()["detail"]
    assert engine.calls == []


def test_a_missing_report_is_404(tmp_path):
    engine = FakeEngine()
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path)
        response = _try(client, game_key="chesscom:other", move_san="e4")

    assert response.status_code == 404
    assert "chesscom:other" in response.json()["detail"]
    assert engine.calls == []


def test_a_corrupt_report_is_409(tmp_path):
    broken = {**REPORT, "moves": [*REPORT["moves"][:2], "not-a-move"]}
    engine = FakeEngine()
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path, report=broken)
        response = _try(client, ply=1, move_san="e4")

    assert response.status_code == 409
    assert "cannot be replayed" in response.json()["detail"]
    assert engine.calls == []


def test_the_body_cannot_choose_a_fen_or_cost(tmp_path):
    engine = FakeEngine()
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path)
        response = client.post(
            f"/api/games/{KEY}/try",
            json={
                "user_color": "white",
                "ply": 1,
                "move_san": "e4",
                "fen": chess.STARTING_FEN,
                "depth": 30,
            },
        )

    assert response.status_code == 422
    assert engine.calls == []


def test_a_timed_out_search_is_503_and_the_engine_is_closed(tmp_path):
    engine = TimedOutEngine()
    with running_app(tmp_path, engine=engine) as (client, db_path):
        _seed(db_path)
        response = _try(client, ply=1, move_san="d4")

    assert response.status_code == 503
    assert response.json()["detail"] == "try-again evaluation timed out"
    assert engine.closed == 1


# --------------------------------------------------------------------------- #
# cache and limits
# --------------------------------------------------------------------------- #


def test_a_cache_hit_skips_the_engine_search(tmp_path):
    after = chess.Board()
    after.push_san("d4")
    cache = MemoryPositionCache()
    cache.store(
        fen=after.fen(),
        engine_version="Fake Engine 1",
        depth=12,
        multipv=4,
        value=[
            {
                "score": chess.engine.PovScore(
                    chess.engine.Cp(0), after.turn
                ),
                "pv": [next(iter(after.legal_moves))],
            }
        ],
    )

    engine = FakeEngine(lambda _board: pytest.fail("engine must not be searched"))
    with running_app(tmp_path, engine=engine, cache=cache) as (client, db_path):
        _seed(db_path)
        response = _try(client, ply=1, move_san="d4")

    assert response.status_code == 200, response.text
    assert response.json()["accepted"] is True
    assert engine.calls == []
    assert engine.closed == 1


def test_the_try_limit_is_separate_and_invalid_requests_do_not_consume_it(tmp_path):
    limiter = RateLimiter(limit=1)
    engine = FakeEngine()
    with running_app(tmp_path, engine=engine, try_limiter=limiter) as (
        client,
        db_path,
    ):
        _seed(db_path)
        invalid = _try(client, ply=1, move_san="Qh9")
        first = _try(client, ply=1, move_san="e4")
        denied = _try(client, ply=1, move_san="e4")

    assert invalid.status_code == 422
    assert first.status_code == 200
    assert denied.status_code == 429
    assert denied.json()["detail"] == (
        "try-again rate limit exceeded; try again later"
    )
    assert denied.headers["X-RateLimit-Limit"] == "1"
    assert denied.headers["X-RateLimit-Remaining"] == "0"
    assert int(denied.headers["Retry-After"]) >= 1
    assert first.headers["X-RateLimit-Remaining"] == "0"
