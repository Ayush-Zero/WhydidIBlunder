"""Generate (or deliberately refresh) a frozen report fixture from a PGN.

Needs a real Stockfish process. The fixture records the engine name, depth and
multipv in its ``engine`` header, so an engine bump shows up as a reviewable diff
rather than a silent change.

Examples
--------
    .\\.venv\\\\Scripts\\\\python.exe scripts\\\\gen_fixture.py ^
        --pgn-file=samples/immortal_game.pgn --color=white --depth=12 ^
        --out=tests/fixtures/immortal_game.report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer.config import (  # noqa: E402
    stockfish_hash_mb,
    stockfish_path,
    stockfish_threads,
)
from analyzer.engine import Engine  # noqa: E402
from analyzer.report import analyze_game, validate_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a frozen report fixture")
    parser.add_argument("--pgn-file", type=Path, required=True)
    parser.add_argument("--color", choices=["white", "black"], default="white")
    parser.add_argument("--depth", type=int, default=12)
    parser.add_argument("--multipv", type=int, default=4)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stockfish", default=None,
                        help="path to the Stockfish binary (default from .env)")
    args = parser.parse_args(argv)

    if not args.pgn_file.exists():
        print(f"error: file not found: {args.pgn_file}", file=sys.stderr)
        return 2

    pgn_text = args.pgn_file.read_text(encoding="utf-8")
    engine_path = args.stockfish or stockfish_path()

    with Engine(
        engine_path, threads=stockfish_threads(), hash_mb=stockfish_hash_mb()
    ) as engine:
        print(f"engine: {engine.name}")
        report = analyze_game(
            pgn_text, args.color, engine=engine, depth=args.depth, multipv=args.multipv
        )

    problems = validate_report(report)
    if problems:
        print("refusing to write an invalid report:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {args.out} "
        f"({len(report['moves'])} moves, accuracy {report['summary']['accuracy']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
