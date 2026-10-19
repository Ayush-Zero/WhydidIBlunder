"""C2 — the deterministic golden game (offline, no engine).

``tests/fixtures/immortal_game.report.json`` is a frozen snapshot of::

    analyze_game(samples/immortal_game.pgn, "white", depth=12, multipv=4)

taken with the configured Stockfish. The fixture carries its provenance in its
``engine`` header; the constants below **pin** that header and the game's exact
flags, so:

* the test runs with no engine at all, and
* if the fixture is regenerated under a different engine / depth / multipv,
  ``test_fixture_provenance_is_pinned`` fails with an instruction to review the
  diff instead of silently accepting new numbers.

**Regenerating the fixture** (needs a real Stockfish; overwrites it, so expect a
large, reviewable diff):

    .\\.venv\\Scripts\\python.exe scripts\\gen_fixture.py ^
        --pgn-file=samples\\immortal_game.pgn --color=white --depth=12 ^
        --out=tests/fixtures/immortal_game.report.json

After regenerating, the diffs here are expected to be *tiny and explainable*
(engine output only). If the flags move, update the constants **only once you
agree with the new calls** — these tables are the agreed judgement of the sample
game, not just whatever the engine emitted.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "immortal_game.report.json"

# ---- provenance (regenerate under a new engine -> this test must fail) ---- #
EXPECTED_ENGINE = "Stockfish 19"
EXPECTED_DEPTH = 12
EXPECTED_MULTIPV = 4
EXPECTED_USER_COLOR = "white"
EXPECTED_MOVE_COUNT = 45

# ---- the game's exact flags: ply -> classification ------------------------- #
EXPECTED_FLAGS = {
    "white": {15: "inaccuracy", 19: "inaccuracy", 35: "mistake"},
    "black": {22: "mistake", 32: "inaccuracy", 36: "mistake", 40: "blunder"},
}

# ---- informational + rule tags --------------------------------------------- #
EXPECTED_TAGS = {
    35: ["fork"],
    36: ["missed_capture"],
    40: ["allowed_mate", "missed_capture"],
    44: ["allowed_mate"],
}

EXPECTED_SUMMARY = {
    "user_color": "white",
    "moves_analysed": 23,
    "accuracy": 87.9,
    "blunders": 0,
    "mistakes": 1,
    "inaccuracies": 2,
    "ok": 20,
}

# Rule tags (B6) explain a *flagged* move; mate tags stay informational.
RULE_TAGS = {"hanging_piece", "missed_capture", "fork", "pin"}


def load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# provenance — the engine-bump tripwire
# --------------------------------------------------------------------------- #
def test_fixture_provenance_is_pinned():
    engine = load()["engine"]
    assert engine["name"] == EXPECTED_ENGINE, (
        f"the golden fixture was produced by {engine['name']!r}, but this test "
        f"expects {EXPECTED_ENGINE!r}. If this is a deliberate engine bump, run "
        "scripts/gen_fixture.py, review the diff move by move, then update "
        "EXPECTED_ENGINE (and the flag/tag tables) in this file."
    )
    assert engine["depth"] == EXPECTED_DEPTH, (
        f"fixture was generated at depth {engine['depth']}, expected "
        f"{EXPECTED_DEPTH}; review the diff before updating the constant."
    )
    assert engine["multipv"] == EXPECTED_MULTIPV


def test_fixture_game_identity():
    report = load()
    assert report["game"]["event"] == "Immortal Game"
    assert report["game"]["white"] == "Adolf Anderssen"
    assert report["game"]["result"] == "1-0"
    assert report["game"]["user_color"] == EXPECTED_USER_COLOR
    assert len(report["moves"]) == EXPECTED_MOVE_COUNT


# --------------------------------------------------------------------------- #
# the flags — "exactly these plies, nothing else"
# --------------------------------------------------------------------------- #
def test_flags_are_exactly_the_expected_plies():
    report = load()
    for side, expected in EXPECTED_FLAGS.items():
        actual = {
            mv["ply"]: mv["classification"]
            for mv in report["moves"]
            if mv["side"] == side and mv["classification"] != "ok"
        }
        assert actual == expected, (
            f"{side} flags drifted.\n  expected: {expected}\n  actual:   {actual}"
        )


def test_summary_matches_the_pinned_counts():
    assert load()["summary"] == EXPECTED_SUMMARY


def test_mating_move_is_not_annotated_with_rule_tags():
    """The checkmating ply (45.Be7#) must stay clean — a tag there reads as a bug."""
    report = load()
    mating = [mv for mv in report["moves"] if mv["san"] == "Be7#"]
    assert mating, "the sample should end in Be7#"
    assert mating[0]["classification"] == "ok"
    assert mating[0]["tags"] == []


def test_rule_tags_only_annotate_flagged_moves():
    for mv in load()["moves"]:
        if mv["classification"] != "ok":
            continue
        unexpected = RULE_TAGS & set(mv["tags"])
        assert not unexpected, (
            f"ply {mv['ply']} is '{mv['classification']}' but carries rule tag(s) "
            f"{sorted(unexpected)} — rule tags must explain a flagged move only"
        )


def test_tags_are_exactly_the_expected_plies():
    actual = {
        mv["ply"]: mv["tags"] for mv in load()["moves"] if mv["tags"]
    }
    assert actual == EXPECTED_TAGS


# --------------------------------------------------------------------------- #
# the fixture must stay self-consistent (guards the maths, not the engine)
# --------------------------------------------------------------------------- #
def test_every_tagged_move_has_a_reason():
    """A tag on a suppressed 'ok' move is allowed (mate tags), but never on
    a move with no classification to explain."""
    for mv in load()["moves"]:
        if mv["tags"]:
            # either it is flagged, or it carries an informational mate tag
            assert mv["classification"] != "ok" or (
                set(mv["tags"]) & {"missed_mate", "allowed_mate"}
            ), mv
