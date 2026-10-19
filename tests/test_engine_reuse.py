"""C1 — engine wiring: position reuse + config-driven settings (offline).

No engine is launched. A fake analyser records every ``analyse`` call so we can
prove two things the Milestone-C breakdown asks for:

* **Reuse halves the work.** Each position is searched exactly once and reused
  as the next move's "before", so an ``n``-ply game costs ``n + 1`` searches —
  not the naive ``2n``.
* **Nothing is hardcoded.** ``depth``/``multipv``/``threads``/``hash`` all flow
  from config (env), and explicit arguments still override it.
"""

from __future__ import annotations

import chess
import chess.engine

from analyzer import config, report
from analyzer.pipeline import analyse_game_moves, read_game

# A short game with no terminal position: 6 plies -> 7 searchable positions.
PGN = """[Event "c1-test"]
[White "A"]
[Black "B"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *
"""


class FakeEngine:
    """Stands in for a Stockfish process: records calls, returns a score."""

    def __init__(self, name: str = "Fake Engine 1", threads=None, hash_mb=None):
        self.name = name
        self.threads = threads
        self.hash_mb = hash_mb
        self.calls: list[tuple[str, int, int]] = []

    def analyse(self, board: chess.Board, depth: int, multipv: int) -> list:
        self.calls.append((board.fen(), depth, multipv))
        # Board-dependent so different positions yield different evals.
        cp = 10 * board.fullmove_number + (0 if board.turn == chess.WHITE else 1)
        score = chess.engine.PovScore(chess.engine.Cp(cp), board.turn)
        first = next(iter(board.legal_moves))  # a legal PV first move
        return [{"score": score, "pv": [first]} for _ in range(multipv)]

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
# reuse
# --------------------------------------------------------------------------- #
def test_each_position_is_searched_exactly_once():
    game = read_game(PGN)
    n = len(list(game.mainline_moves()))
    engine = FakeEngine()

    moves = analyse_game_moves(game, engine=engine, depth=5, multipv=2)

    assert len(moves) == n
    # n plies -> n + 1 positions, each searched once.
    assert len(engine.calls) == n + 1
    # The naive "search before AND after every move" would be 2n.
    assert len(engine.calls) < 2 * n
    # ...and those are n + 1 *distinct* positions, i.e. no wasted re-search.
    assert len({fen for fen, _, _ in engine.calls}) == n + 1


def test_after_eval_is_the_reused_before_of_the_next_move():
    """The seam between moves is one shared search, so the evals mirror."""
    moves = analyse_game_moves(read_game(PGN), engine=FakeEngine(), depth=5, multipv=2)
    for current, following in zip(moves, moves[1:]):
        # current.eval_after is the opponent-to-move board, so it flips sign
        # when re-read from the next mover's point of view.
        assert current.eval_after_cp == -following.eval_before_cp


# --------------------------------------------------------------------------- #
# config-driven settings
# --------------------------------------------------------------------------- #
def test_depth_and_multipv_come_from_config(monkeypatch):
    monkeypatch.setenv("ANALYSIS_DEPTH", "7")
    monkeypatch.setenv("ANALYSIS_MULTIPV", "3")
    engine = FakeEngine()

    report.analyze_game(PGN, "white", engine=engine)

    assert engine.calls
    assert {depth for _, depth, _ in engine.calls} == {7}
    assert {multipv for _, _, multipv in engine.calls} == {3}


def test_pipeline_defaults_to_config_when_called_directly(monkeypatch):
    monkeypatch.setenv("ANALYSIS_DEPTH", "9")
    monkeypatch.setenv("ANALYSIS_MULTIPV", "2")
    engine = FakeEngine()

    analyse_game_moves(read_game(PGN), engine=engine)

    assert {depth for _, depth, _ in engine.calls} == {9}
    assert {multipv for _, _, multipv in engine.calls} == {2}


def test_explicit_arguments_override_config(monkeypatch):
    monkeypatch.setenv("ANALYSIS_DEPTH", "7")
    monkeypatch.setenv("ANALYSIS_MULTIPV", "3")
    engine = FakeEngine()

    report.analyze_game(PGN, "white", engine=engine, depth=2, multipv=1)

    assert {depth for _, depth, _ in engine.calls} == {2}
    assert {multipv for _, _, multipv in engine.calls} == {1}


def test_threads_hash_and_path_come_from_config(monkeypatch):
    monkeypatch.setenv("STOCKFISH_PATH", "fake-stockfish")
    monkeypatch.setenv("STOCKFISH_THREADS", "2")
    monkeypatch.setenv("STOCKFISH_HASH_MB", "64")
    captured: dict = {}

    class SpyEngine(FakeEngine):
        def __init__(self, path, threads=1, hash_mb=256):
            super().__init__()
            captured.update(path=path, threads=threads, hash_mb=hash_mb)

    monkeypatch.setattr(report, "Engine", SpyEngine)

    report.analyze_game(PGN, "white")  # no engine -> built from config

    assert captured == {"path": "fake-stockfish", "threads": 2, "hash_mb": 64}


def test_config_defaults_are_not_magic_numbers():
    """The pipeline defaults must track config, whatever the env says."""
    engine = FakeEngine()
    analyse_game_moves(read_game(PGN), engine=engine)
    expected_depth = config.analysis_depth()
    expected_multipv = config.analysis_multipv()
    assert {depth for _, depth, _ in engine.calls} == {expected_depth}
    assert {multipv for _, _, multipv in engine.calls} == {expected_multipv}
