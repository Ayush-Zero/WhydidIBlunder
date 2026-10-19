"""Shared pytest configuration.

The suite is **offline by default**. The only tests that launch Stockfish are
those marked ``live``; ``pytest.ini`` deselects them, and this hook skips them
as a second line of defence unless they are explicitly opted into:

    RUN_LIVE_TESTS=1 pytest -m live
"""

from __future__ import annotations

import os

import pytest

_TRUTHY = {"1", "true", "yes", "on"}


def _live_enabled() -> bool:
    return os.getenv("RUN_LIVE_TESTS", "").strip().lower() in _TRUTHY


def pytest_collection_modifyitems(config, items):
    if _live_enabled():
        return
    skip = pytest.mark.skip(
        reason="live engine test; set RUN_LIVE_TESTS=1 to run "
        "(e.g. RUN_LIVE_TESTS=1 pytest -m live)"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
