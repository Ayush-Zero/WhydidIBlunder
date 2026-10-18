"""Phase 0 validation spike.

Analyse games and print per-move flags (single game) or a per-game summary
(batch), so you can eyeball the classifier and tune the thresholds. No web, no
database.

Examples
--------
    # one game, full move table (~depth 12 is a good balance for eyeballing)
    .\\.venv\\Scripts\\python.exe scripts\\phase0_report.py --pgn-file samples\\immortal_game.pgn --color white --depth 12

    # sweep every game in an export, summary per game (needs white/black)
    .\\.venv\\Scripts\\python.exe scripts\\phase0_report.py --pgn-file my_games.pgn --color black --all-games --depth 12

    # one game from a multi-game file, only the flagged moves
    .\\.venv\\Scripts\\python.exe scripts\\phase0_report.py --pgn-file my_games.pgn --game-index 3 --color black --only-flags
"""

from __future__ import annotations

import argparse
import io
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
from analyzer.winpct import accuracy_from_win_diff  # noqa: E402

FLAG_ORDER = ["blunder", "mistake", "inaccuracy"]
_EXPORTER = chess.pgn.StringExporter(headers=True, comments=False)


# --------------------------------------------------------------------------- #
# formatting helpers
# --------------------------------------------------------------------------- #
def fmt_eval(cp: int) -> str:
    if cp >= MATE_CP - 1000:
        return f"+M{max(1, round((MATE_CP - cp) / 10))}"
    if cp <= -(MATE_CP - 1000):
        return f"-M{max(1, round((MATE_CP + cp) / 10))}"
    return f"{cp / 100:+.2f}"


def game_accuracy(reports: list[MoveReport], color: str) -> float | None:
    moves = [r for r in reports if r.side == color]
    if not moves:
        return None
    return sum(accuracy_from_win_diff(r.drop) for r in moves) / len(moves)


def count_flags(reports: list[MoveReport], color: str) -> dict[str, int]:
    counts = {name: 0 for name in FLAG_ORDER}
    for r in reports:
        if (color == "both" or r.side == color) and r.classification in counts:
            counts[r.classification] += 1
    return counts


# --------------------------------------------------------------------------- #
# PGN helpers
# --------------------------------------------------------------------------- #
def iter_games(text: str):
    stream = io.StringIO(text)
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            return
        yield game


def select_game_text(text: str, game_index: int) -> str:
    if game_index == 0:
        return text
    for i, game in enumerate(iter_games(text)):
        if i == game_index:
            return game.accept(_EXPORTER)
    raise SystemExit(f"no game at index {game_index}")


def color_of(game: chess.pgn.Game, me: str | None) -> str | None:
    """Which side did ``me`` play? None if unknown."""
    if not me:
        return None
    me_l = me.lower()
    if game.headers.get("White", "").lower() == me_l:
        return "white"
    if game.headers.get("Black", "").lower() == me_l:
        return "black"
    return None


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #
def print_table(reports: list[MoveReport], color: str) -> None:
    header = (
        f"{'ply':>4}  {'move':>6}  {'side':>5}  {'played':>7}  {'best':>7}  "
        f"{'before':>7}  {'after':>7}  {'drop':>6}  {'class':>11}  {'rank':>4}  notes"
    )
    print(header)
    print("-" * len(header))
    for r in reports:
        if color != "both" and r.side != color:
            continue
        rank = r.played_rank if r.played_rank is not None else "-"
        notes = ",".join(r.tags)
        if r.suppressed and r.suppress_reason:
            notes = (notes + " " if notes else "") + f"[ok: {r.suppress_reason}]"
        dot = "." if r.side == "white" else "..."
        print(
            f"{r.ply:>4}  {r.move_number:>5}{dot}  {r.side:>5}  "
            f"{r.played_san:>7}  {r.best_san:>7}  {fmt_eval(r.eval_before_cp):>7}  "
            f"{fmt_eval(r.eval_after_cp):>7}  {r.drop:>6.1f}  "
            f"{r.classification:>11}  {str(rank):>4}  {notes}"
        )


def print_summary(reports: list[MoveReport], color: str) -> None:
    counts = count_flags(reports, color)
    selected = [r for r in reports if color == "both" or r.side == color]
    acc = game_accuracy(reports, color)
    label = "both sides" if color == "both" else color
    print()
    print(f"Summary ({label}, {len(selected)} moves):")
    for name in FLAG_ORDER:
        print(f"  {name:<11} {counts[name]}")
    print(f"  {'ok':<11} {len(selected) - sum(counts.values())}")
    if acc is not None:
        print(f"  {'accuracy':<11} {acc:.1f}")


def print_batch(rows: list[dict]) -> None:
    header = (
        f"{'#':>3}  {'white':<22}  {'black':<22}  {'res':>4}  {'you':>4}  "
        f"{'acc':>5}  {'bl':>3}  {'mi':>3}  {'in':>3}"
    )
    print(header)
    print("-" * len(header))
    totals = {name: 0 for name in FLAG_ORDER}
    accs: list[float] = []
    for row in rows:
        left, right = row["white"], row["black"]
        acc = row["accuracy"]
        accs.append(acc) if acc is not None else None
        for name in FLAG_ORDER:
            totals[name] += row["counts"][name]
        acc_str = f"{acc:.1f}" if acc is not None else "-"
        print(
            f"{row['index']:>3}  {left[:22]:<22}  {right[:22]:<22}  "
            f"{row['result']:>4}  {row['you_moves']:>4}  {acc_str:>5}  "
            f"{row['counts']['blunder']:>3}  {row['counts']['mistake']:>3}  "
            f"{row['counts']['inaccuracy']:>3}"
        )
    print("-" * len(header))
    mean_acc = f"{sum(accs) / len(accs):.1f}" if accs else "-"
    print(
        f"{'TOT':>3}  {f'{len(rows)} games':<22}  {'':<22}  {'':>4}  {'':>4}  "
        f"{mean_acc:>5}  {totals['blunder']:>3}  {totals['mistake']:>3}  "
        f"{totals['inaccuracy']:>3}"
    )


# --------------------------------------------------------------------------- #
# drivers
# --------------------------------------------------------------------------- #
def run_single(text: str, engine: Engine, depth: int, multipv: int,
               color: str, only_flags: bool) -> None:
    reports = analyze_game(text, engine=engine, depth=depth, multipv=multipv)
    shown = [r for r in reports if r.classification != "ok"] if only_flags else reports
    print_table(shown, color)
    print_summary(reports, color)


def run_batch(text: str, engine: Engine, depth: int, multipv: int, color: str,
              me: str | None = None) -> None:
    rows = []
    for i, game in enumerate(iter_games(text)):
        if not list(game.mainline_moves()):
            continue
        white = game.headers.get("White", "?")
        black = game.headers.get("Black", "?")
        result = game.headers.get("Result", "?")
        game_color = color_of(game, me) or color
        print(f"analysing game {i}: {white} vs {black} (you={game_color}) ...",
              flush=True)
        reports = analyze_game(
            game.accept(_EXPORTER), engine=engine, depth=depth, multipv=multipv
        )
        rows.append(
            {
                "index": i,
                "white": white,
                "black": black,
                "result": result,
                "you_moves": sum(1 for r in reports if r.side == game_color),
                "counts": count_flags(reports, game_color),
                "accuracy": game_accuracy(reports, game_color),
            }
        )
    print()
    print_batch(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 0 chess blunder report")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--pgn-file", type=Path, help="path to a .pgn file")
    src.add_argument("--pgn", type=str, help="inline PGN text")
    parser.add_argument("--color", choices=["white", "black", "both"], default="both",
                        help="which side is 'you' (default: both)")
    parser.add_argument("--me", default=None,
                        help="your Chess.com username; detect your side per game "
                             "(overrides --color; needed when you play both colours)")
    parser.add_argument("--all-games", action="store_true",
                        help="sweep every game in the file -> per-game summary")
    parser.add_argument("--game-index", type=int, default=0,
                        help="which game in a multi-game PGN (default: 0)")
    parser.add_argument("--depth", type=int, default=None,
                        help=f"search depth (default from .env: {analysis_depth()})")
    parser.add_argument("--multipv", type=int, default=None,
                        help=f"MultiPV lines (default from .env: {analysis_multipv()})")
    parser.add_argument("--only-flags", action="store_true",
                        help="hide 'ok' moves (single-game mode)")
    parser.add_argument("--stockfish", type=str, default=None,
                        help="path to the Stockfish binary (default from .env)")
    args = parser.parse_args(argv)

    if args.all_games and args.color == "both" and not args.me:
        parser.error("--all-games needs --color white/black, or --me <username>")

    depth = args.depth if args.depth is not None else analysis_depth()
    multipv = args.multipv if args.multipv is not None else analysis_multipv()
    engine_path = args.stockfish or stockfish_path()

    if args.pgn is not None:
        pgn_text = args.pgn
    else:
        if not args.pgn_file.exists():
            print(f"error: file not found: {args.pgn_file}", file=sys.stderr)
            return 2
        pgn_text = args.pgn_file.read_text(encoding="utf-8")

    print(f"Stockfish: {engine_path}")
    who = f"you={args.me}" if args.me else f"color={args.color}"
    print(f"depth={depth}  multipv={multipv}  {who}  perspective=each mover")
    print()

    try:
        with Engine(engine_path, threads=stockfish_threads(),
                    hash_mb=stockfish_hash_mb()) as engine:
            print(f"engine: {engine.name}")
            print()
            if args.all_games:
                run_batch(pgn_text, engine, depth, multipv, args.color, args.me)
            else:
                text = select_game_text(pgn_text, args.game_index)
                color = args.color
                if args.me:
                    game = chess.pgn.read_game(io.StringIO(text))
                    color = color_of(game, args.me) or color
                run_single(
                    text, engine, depth, multipv, color, args.only_flags,
                )
    except FileNotFoundError:
        print(
            f"error: could not launch Stockfish at '{engine_path}'.\n"
            "Set STOCKFISH_PATH in .env or pass --stockfish.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
