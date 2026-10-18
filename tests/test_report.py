"""Characterisation tests for the frozen report fixture (offline, no engine).

These anchor the classification logic: given the frozen evals, the report's
``drop`` and ``classification`` must recompute exactly. If a refactor changes the
maths or the precedence, one of these fails.

The fixture is regenerated deliberately with ``scripts/gen_fixture.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from analyzer.report import SCHEMA_VERSION, validate_report
from analyzer.winpct import accuracy_from_win_diff, classify, win_pct

FIXTURE = Path(__file__).parent / "fixtures" / "immortal_game.report.json"


def load_report() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_passes_validation():
    assert validate_report(load_report()) == []


def test_fixture_has_current_schema_version():
    assert load_report()["schema_version"] == SCHEMA_VERSION


def test_fixture_records_engine_provenance():
    engine = load_report()["engine"]
    assert engine["name"]  # non-empty, e.g. "Stockfish 19"
    assert engine["depth"] >= 1
    assert engine["multipv"] >= 1


def test_drop_and_classification_recompute_from_evals():
    for mv in load_report()["moves"]:
        drop = win_pct(mv["eval_before_cp"]) - win_pct(mv["eval_after_cp"])
        assert abs(drop - mv["drop_win_pct"]) < 1e-9, mv
        assert classify(drop, mv["suppressed"]) == mv["classification"], mv


def test_accuracy_recomputes_from_drops():
    report = load_report()
    color = report["summary"]["user_color"]
    selected = [m for m in report["moves"] if m["side"] == color]
    expected = round(
        sum(accuracy_from_win_diff(m["drop_win_pct"]) for m in selected) / len(selected),
        1,
    )
    assert expected == report["summary"]["accuracy"]


def test_validation_catches_a_corrupted_report():
    """Guard against the validator silently passing everything."""
    report = load_report()
    report["summary"]["blunders"] += 1  # now inconsistent with the moves
    problems = validate_report(report)
    assert any("blunders" in p for p in problems)
