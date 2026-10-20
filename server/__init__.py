"""Phase 2 service layer: storage, the Chess.com client, and (later) the API.

This package is deliberately separate from :mod:`analyzer`, which stays a pure,
web-free library (Phase 1's contract). Nothing here imports FastAPI yet — Milestone
A is schema + client only.
"""

from __future__ import annotations

__all__ = ["chesscom", "db"]
