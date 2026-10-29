"""Thin wrapper around a Stockfish UCI process."""

from __future__ import annotations

import concurrent.futures

import chess
import chess.engine

# Centipawn magnitude used to encode a forced mate so it saturates win_pct.
# Mate distance is preserved (``10000 - mate_in * 10``) so M1 vs M3 differ.
MATE_CP = 10_000


def mate_to_cp(mate: int) -> int:
    """Encode a mate score (moves to mate, sign = who is mating) as centipawns."""
    return (MATE_CP - abs(mate) * 10) * (1 if mate > 0 else -1)


class EngineTimeoutError(Exception):
    """Raised when analysis exceeds the configured time limit."""


class Engine:
    """A single Stockfish process. Use as a context manager."""

    def __init__(self, path: str, threads: int = 1, hash_mb: int = 256):
        self._engine = chess.engine.SimpleEngine.popen_uci(path)
        self._engine.configure({"Threads": threads, "Hash": hash_mb})
        self.name: str = self._engine.id.get("name", "unknown")

    def analyse(self, board: chess.Board, depth: int, multipv: int, timeout: float | None = None) -> list:
        """Analyse a position, always returning a list of Info (best first)."""
        if timeout is None or timeout <= 0:
            infos = self._engine.analyse(
                board, chess.engine.Limit(depth=depth), multipv=multipv
            )
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self._engine.analyse,
                    board,
                    chess.engine.Limit(depth=depth),
                    multipv=multipv,
                )
                try:
                    infos = future.result(timeout=timeout)
                except concurrent.futures.TimeoutError as exc:
                    # Try to kill the engine so it doesn't leak
                    try:
                        self._engine.quit()
                    except Exception:
                        try:
                            self._engine.close()
                        except Exception:
                            pass
                    raise EngineTimeoutError(f"analysis timed out after {timeout}s") from exc
        if isinstance(infos, dict):  # multipv == 1 returns a lone dict
            infos = [infos]
        return list(infos)

    def close(self) -> None:
        try:
            self._engine.quit()
        except Exception:
            pass

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
