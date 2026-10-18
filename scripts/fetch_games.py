"""Fetch a player's games from the public Chess.com API into one PGN file.

No API key needed. Be polite: we only touch a few recent months and add a small
delay between requests, with a descriptive User-Agent.

Examples
--------
    .\\.venv\\Scripts\\python.exe scripts\\fetch_games.py --username BEAST-ASH --max-games 20

    # only rapid, last 6 months
    .\\.venv\\Scripts\\python.exe scripts\\fetch_games.py --username BEAST-ASH --time-class rapid --months 6
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

API = "https://api.chess.com/pub"
USER_AGENT = "WhydidIBlunder/0.1 (github.com/Ayush-Zero/WhydidIBlunder)"


def _get(client: httpx.Client, url: str) -> dict:
    resp = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def fetch_games(username: str, max_games: int, months: int,
                time_class: str | None) -> list[dict]:
    with httpx.Client(follow_redirects=True) as client:
        archives = _get(client, f"{API}/player/{username}/games/archives")["archives"]
        collected: list[dict] = []
        for archive in reversed(archives[-months:]):
            month = _get(client, archive).get("games", [])
            for game in reversed(month):  # newest first
                if time_class and game.get("time_class") != time_class:
                    continue
                if not game.get("pgn"):
                    continue
                collected.append(game)
                if len(collected) >= max_games:
                    return collected
            time.sleep(0.4)  # be nice
    return collected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch Chess.com games to PGN")
    parser.add_argument("--username", required=True, help="Chess.com username")
    parser.add_argument("--max-games", type=int, default=20,
                        help="how many recent games to keep (default: 20)")
    parser.add_argument("--months", type=int, default=6,
                        help="how many recent months to scan (default: 6)")
    parser.add_argument("--time-class", default=None,
                        choices=["bullet", "blitz", "rapid", "daily"],
                        help="only keep this time control")
    parser.add_argument("--output", type=Path, default=None,
                        help="output PGN path (default: data/<username>_games.pgn)")
    args = parser.parse_args(argv)

    out = args.output or Path("data") / f"{args.username.lower()}_games.pgn"
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"fetching up to {args.max_games} games for '{args.username}' "
          f"(last {args.months} months"
          + (f", {args.time_class})" if args.time_class else ")"))
    games = fetch_games(args.username, args.max_games, args.months, args.time_class)
    if not games:
        print("no games found", file=sys.stderr)
        return 1

    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for game in games:
            fh.write(game["pgn"].strip())
            fh.write("\n\n")

    print(f"wrote {len(games)} games -> {out}")
    classes: dict[str, int] = {}
    for game in games:
        classes[game.get("time_class", "?")] = classes.get(game.get("time_class", "?"), 0) + 1
    print("time classes:", ", ".join(f"{k}={v}" for k, v in classes.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
