"""The stable report contract for :func:`analyze_game`.

This module owns the **shape** of the public output: a JSON-serialisable dict
with an explicit schema version. Everything downstream (API, DB, UI) reads this
shape, so it is documented field-by-field in ``docs/analyzer-schema.md`` and
guarded by :func:`validate_report` and the characterisation tests.

Units and point-of-view, stated once (and enforced by the names):

* ``eval_*_cp``        centipawns, **mover's** POV (mates encoded via ``MATE_CP``).
* ``eval_*_win_pct``   win%-points (0-100), mover's POV.
* ``drop_win_pct``     win%-points (0-100); positive = worse for the mover.
* ``mate_*``           signed mate distance in moves, or ``None``; positive means
                       the mover is delivering mate.
"""

from __future__ import annotations

import chess.pgn

from .config import (
    analysis_depth,
    analysis_multipv,
    stockfish_hash_mb,
    stockfish_path,
    stockfish_threads,
)
from .engine import Engine
from .pipeline import MoveReport, analyse_game_moves, read_game
from .winpct import accuracy_from_win_diff, win_pct

SCHEMA_VERSION = 1

CLASSIFICATIONS = ("ok", "inaccuracy", "mistake", "blunder")
SIDES = ("white", "black")


# --------------------------------------------------------------------------- #
# move -> dict
# --------------------------------------------------------------------------- #
def move_to_dict(m: MoveReport) -> dict:
    """Serialise one :class:`MoveReport` to the wire shape."""
    return {
        "ply": m.ply,
        "move_number": m.move_number,
        "side": m.side,
        "san": m.played_san,
        "best_san": m.best_san,
        "pv_san": list(m.pv_san),
        "eval_before_cp": m.eval_before_cp,
        "eval_after_cp": m.eval_after_cp,
        "eval_before_win_pct": win_pct(m.eval_before_cp),
        "eval_after_win_pct": win_pct(m.eval_after_cp),
        "mate_before": m.mate_before,
        "mate_after": m.mate_after,
        "played_rank": m.played_rank,
        "drop_win_pct": m.drop,
        "classification": m.classification,
        "tags": list(m.tags),
        "suppressed": m.suppressed,
        "suppress_reason": m.suppress_reason,
    }


# --------------------------------------------------------------------------- #
# summary
# --------------------------------------------------------------------------- #
def summarize(moves: list[MoveReport], color: str) -> dict:
    """Counts + accuracy for ``color``'s moves only."""
    selected = [m for m in moves if m.side == color]
    counts = {c: 0 for c in CLASSIFICATIONS}
    for m in selected:
        counts[m.classification] = counts.get(m.classification, 0) + 1
    accuracy = None
    if selected:
        accuracy = sum(accuracy_from_win_diff(m.drop) for m in selected) / len(selected)
    return {
        "user_color": color,
        "moves_analysed": len(selected),
        "accuracy": round(accuracy, 1) if accuracy is not None else None,
        "blunders": counts["blunder"],
        "mistakes": counts["mistake"],
        "inaccuracies": counts["inaccuracy"],
        "ok": counts["ok"],
    }


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def build_report(
    game: chess.pgn.Game,
    moves: list[MoveReport],
    color: str,
    engine_name: str,
    depth: int,
    multipv: int,
) -> dict:
    """Assemble the public report dict from analysed moves."""
    headers = game.headers
    return {
        "schema_version": SCHEMA_VERSION,
        "engine": {"name": engine_name, "depth": depth, "multipv": multipv},
        "game": {
            "white": headers.get("White", ""),
            "black": headers.get("Black", ""),
            "result": headers.get("Result", ""),
            "date": headers.get("Date", ""),
            "event": headers.get("Event", ""),
            "eco": headers.get("ECO", ""),
            "link": headers.get("Link", "") or headers.get("Site", ""),
            "user_color": color,
        },
        "moves": [move_to_dict(m) for m in moves],
        "summary": summarize(moves, color),
    }


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def analyze_game(
    pgn_text: str,
    color: str,
    *,
    engine: Engine | None = None,
    depth: int | None = None,
    multipv: int | None = None,
) -> dict:
    """Analyse the first game in ``pgn_text`` for ``color`` and return the report dict.

    ``color`` must be ``"white"`` or ``"black"`` — it selects whose moves the
    summary covers. If no ``engine`` is supplied, one is launched from config and
    closed on exit. ``depth``/``multipv`` default to the configured values.

    The labels and ``summary.accuracy`` are our own win%-based model (a
    Lichess-style curve plus win%-point thresholds). They are close in spirit to
    Chess.com's numbers but are **not** guaranteed to match them, and a shallower
    depth will disagree with a deeper one on sharp positions. The returned dict
    is JSON-serialisable.
    """
    color = (color or "").lower()
    if color not in SIDES:
        raise ValueError("color must be 'white' or 'black'")

    depth = analysis_depth() if depth is None else depth
    multipv = analysis_multipv() if multipv is None else multipv

    own_engine = engine is None
    if own_engine:
        engine = Engine(
            stockfish_path(),
            threads=stockfish_threads(),
            hash_mb=stockfish_hash_mb(),
        )
    try:
        game = read_game(pgn_text)
        moves = analyse_game_moves(game, engine=engine, depth=depth, multipv=multipv)
        return build_report(game, moves, color, engine.name, depth, multipv)
    finally:
        if own_engine:
            engine.close()


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _is_int(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x: object) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _validate_move(mv: object, i: int, err) -> None:
    where = f"moves[{i}]"
    if not isinstance(mv, dict):
        err(f"{where} must be a dict")
        return
    for k in ("ply", "move_number"):
        if not _is_int(mv.get(k)) or mv[k] < 1:
            err(f"{where}.{k} must be a positive int, got {mv.get(k)!r}")
    if mv.get("side") not in SIDES:
        err(f"{where}.side must be one of {SIDES}")
    for k in ("san", "best_san"):
        if not isinstance(mv.get(k), str) or not mv[k]:
            err(f"{where}.{k} must be a non-empty string")
    if mv.get("classification") not in CLASSIFICATIONS:
        err(f"{where}.classification must be one of {CLASSIFICATIONS}")
    for k in ("eval_before_cp", "eval_after_cp"):
        if not _is_int(mv.get(k)):
            err(f"{where}.{k} must be an int")
    for k in ("eval_before_win_pct", "eval_after_win_pct", "drop_win_pct"):
        if not _is_num(mv.get(k)):
            err(f"{where}.{k} must be a number")
    for k in ("mate_before", "mate_after"):
        v = mv.get(k)
        if v is not None and not _is_int(v):
            err(f"{where}.{k} must be null or an int")
    rank = mv.get("played_rank")
    if rank is not None and (not _is_int(rank) or rank < 1):
        err(f"{where}.played_rank must be null or a positive int")
    if not isinstance(mv.get("pv_san"), list) or not all(
        isinstance(s, str) for s in mv["pv_san"]
    ):
        err(f"{where}.pv_san must be a list of strings")
    if not isinstance(mv.get("tags"), list) or not all(
        isinstance(s, str) for s in mv["tags"]
    ):
        err(f"{where}.tags must be a list of strings")
    if not isinstance(mv.get("suppressed"), bool):
        err(f"{where}.suppressed must be a bool")
    if not isinstance(mv.get("suppress_reason"), str):
        err(f"{where}.suppress_reason must be a string")
    if mv.get("suppressed") and mv.get("classification") != "ok":
        err(f"{where}: a suppressed move must be classified 'ok'")


def _validate_summary(summary: object, moves: object, err) -> None:
    if not isinstance(summary, dict):
        err("summary must be a dict")
        return
    if summary.get("user_color") not in SIDES:
        err("summary.user_color must be 'white' or 'black'")
    if not _is_int(summary.get("moves_analysed")) or summary["moves_analysed"] < 0:
        err("summary.moves_analysed must be a non-negative int")
    acc = summary.get("accuracy")
    if acc is not None and (not _is_num(acc) or not (0 <= acc <= 100)):
        err("summary.accuracy must be null or a number in [0, 100]")
    for k in ("blunders", "mistakes", "inaccuracies", "ok"):
        if not _is_int(summary.get(k)) or summary[k] < 0:
            err(f"summary.{k} must be a non-negative int")

    # consistency between summary and moves
    if isinstance(moves, list) and summary.get("user_color") in SIDES:
        selected = [
            m
            for m in moves
            if isinstance(m, dict) and m.get("side") == summary["user_color"]
        ]
        if _is_int(summary.get("moves_analysed")) and summary["moves_analysed"] != len(
            selected
        ):
            err(
                f"summary.moves_analysed ({summary['moves_analysed']}) does not match "
                f"the number of {summary['user_color']} moves ({len(selected)})"
            )
        pairs = (
            ("blunders", "blunder"),
            ("mistakes", "mistake"),
            ("inaccuracies", "inaccuracy"),
            ("ok", "ok"),
        )
        for key, label in pairs:
            n = sum(1 for m in selected if m.get("classification") == label)
            if _is_int(summary.get(key)) and summary[key] != n:
                err(
                    f"summary.{key} ({summary[key]}) does not match the number of "
                    f"'{label}' moves ({n})"
                )


def validate_report(report: object) -> list[str]:
    """Return a list of human-readable problems; empty means valid.

    Pure and dependency-free so it can run offline against a frozen fixture.
    """
    errors: list[str] = []

    def err(msg: str) -> None:
        errors.append(msg)

    if not isinstance(report, dict):
        return [f"report must be a dict, got {type(report).__name__}"]

    if report.get("schema_version") != SCHEMA_VERSION:
        err(
            f"schema_version must be {SCHEMA_VERSION}, "
            f"got {report.get('schema_version')!r}"
        )

    engine = report.get("engine")
    if not isinstance(engine, dict):
        err("engine must be a dict")
    else:
        if not isinstance(engine.get("name"), str) or not engine["name"]:
            err("engine.name must be a non-empty string")
        for k in ("depth", "multipv"):
            if not _is_int(engine.get(k)) or engine[k] < 1:
                err(f"engine.{k} must be a positive int, got {engine.get(k)!r}")

    game = report.get("game")
    if not isinstance(game, dict):
        err("game must be a dict")
    else:
        for k in ("white", "black"):
            if not isinstance(game.get(k), str) or not game[k]:
                err(f"game.{k} must be a non-empty string")
        if game.get("user_color") not in SIDES:
            err("game.user_color must be 'white' or 'black'")
        for k in ("result", "date", "event", "eco", "link"):
            if not isinstance(game.get(k), str):
                err(f"game.{k} must be a string")

    moves = report.get("moves")
    if not isinstance(moves, list):
        err("moves must be a list")
    else:
        for i, mv in enumerate(moves):
            _validate_move(mv, i, err)

    _validate_summary(report.get("summary"), moves, err)

    return errors
