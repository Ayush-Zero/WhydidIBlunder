"""Phase 0 validation spike.

Analyse one game and print a per-move flag table so you can eyeball the
classifier and tune the thresholds. No web, no database.

Examples
--------
    # quick smoke test on the bundled sample (~depth 12)
    .\\.venv\\Scripts\\python.exe scripts\\phase0_report.py --pgn-file samples\\immortal_game.pgn --color white --depth 12

    # your own export, full depth, only your mistakes and worse
    .\\.venv\\Scripts\\python.exe scripts\\phase0_report.py --pgn-file my_games.pgn --game-index 0 --color black --only-flags
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chess.pgn  # noqa: E402

from analyzer.config import (  # noqa: E402
    analysis_depth,
    analysis_multipv,
    stockfish_hash_mb,
    stockfish_path,
    stockfish_threads,
)
from analyzer.engine import MATE_CP, Engine  # noqa: E402
from analyzer.pipeline import MoveReport, analyze_game  # noqa: E402

FLAG_ORDER = ["blunder", "mistake", "inaccuracy"]


def fmt_eval(cp: int) -> str:
    if cp >= MATE_CP - 1000:
        return f"+M{max(1, round((MATE_CP - cp) / 10))}"
    if cp <= -(MATE_CP - 1000):
        return f"-M{max(1, round((MATE_CP + cp) / 10))}"
    return f"{cp / 100:+.2f}"


def load_pgn_text(path: Path, game_index: int) -> str:
    """Return the PGN text for game ``game_index`` from a (possibly multi-game) file."""
    with path.open(encoding="utf-8") as fh:
        stream = fh.read()
    if game_index == 0:
        return stream
    import io

    buf = io.StringIO(stream)
    game = None
    for _ in range(game_index + 1):
        game = chess.pgn.read_game(buf)
        if game is None:
            raise SystemExit(f"No game at index {game_index} in {path}")
    return game.accept(chess.pgn.StringExporter(headers=True, comments=False))


def print_table(reports: list[MoveReport], color: str) -> None:
    header = (
        f"{'ply':>4}  {'move':>6}  {'side':>5}  {'played':>7}  {'best':>7}  "
        f"{'before':>7}  {'after':>7}  {'drop':>6}  {'class':>11}  {'rank':>4}  tags"
    )
    print(header)
    print("-" * len(header))
    for r in reports:
        if color != "both" and r.side != color:
            continue
        rank = r.played_rank if r.played_rank is not None else "-"
        tags = ",".join(r.tags)
        if r.suppressed and r.suppress_reason:
            tags = (tags + " " if tags else "") + f"[suppressed: {r.suppress_reason}]"
        print(
            f"{r.ply:>4}  {r.move_number:>5}{'.' if r.side == 'white' else '...'}  "
            f"{r.side:>5}  {r.played_san:>7}  {r.best_san:>7}  "
            f"{fmt_eval(r.eval_before_cp):>7}  {fmt_eval(r.eval_after_cp):>7}  "
            f"{r.drop:>6.1f}  {r.classification:>11}  {str(rank):>4}  {tags}"
        )


def print_summary(reports: list[MoveReport], color: str) -> None:
    selected = [r for r in reports if color == "both" or r.side == color]
    counts: dict[str, int] = {}
    for r in selected:
        counts[r.classification] = counts.get(r.classification, 0) + 1
    label = "both sides" if color == "both" else color
    print()
    print(f"Summary ({label}, {len(selected)} moves):")
    for name in FLAG_ORDER:
        print(f"  {name:<11} {counts.get(name, 0)}")
    print(f"  {'ok':<11} {counts.get('ok', 0)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 0 chess blunder report")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--pgn-file", type=Path, help="path to a .pgn file")
    src.add_argument("--pgn", type=str, help="inline PGN text")
    parser.add_argument(
        "--color", choices=["white", "black", "both"], default="both",
        help="which side to show (default: both)",
    )
    parser.add_argument("--game-index", type=int, default=0,
                        help="which game in a multi-game PGN (default: 0)")
    parser.add_argument("--depth", type=int, default=None,
                        help=f"search depth (default from .env: {analysis_depth()})")
    parser.add_argument("--multipv", type=int, default=None,
                        help=f"MultiPV lines (default from .env: {analysis_multipv()})")
    parser.add_argument("--only-flags", action="store_true",
                        help="hide 'ok' moves")
    parser.add_argument("--stockfish", type=str, default=None,
                        help="path to the Stockfish binary (default from .env)")
    args = parser.parse_args(argv)

    depth = args.depth if args.depth is not None else analysis_depth()
    multipv = args.multipv if args.multipv is not None else analysis_multipv()
    engine_path = args.stockfish or stockfish_path()

    if args.pgn is not None:
        pgn_text = args.pgn
    else:
        if not args.pgn_file.exists():
            print(f"error: file not found: {args.pgn_file}", file=sys.stderr)
            return 2
        pgn_text = load_pgn_text(args.pgn_file, args.game_index)

    print(f"Stockfish: {engine_path}")
    print(f"depth={depth}  multipv={multipv}  perspective=each mover")
    print()

    try:
        with Engine(
            engine_path, threads=stockfish_threads(), hash_mb=stockfish_hash_mb()
        ) as engine:
            print(f"engine: {engine.name}")
            print()
            reports = analyze_game(pgn_text, engine=engine, depth=depth, multipv=multipv)
    except FileNotFoundError:
        print(
            f"error: could not launch Stockfish at '{engine_path}'.\n"
            "Set STOCKFISH_PATH in .env or pass --stockfish.",
            file=sys.stderr,
        )
        return 2

    if args.only_flags:
        reports = [r for r in reports if r.classification != "ok"]

    print_table(reports, args.color)
    print_summary(
        [r for r in reports], args.color
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
