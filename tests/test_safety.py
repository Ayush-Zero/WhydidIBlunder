"""D5 — the shell tripwire.

The analyser's command-injection safety does not come from escaping anything: it
comes from **never building a command**. Stockfish is the only process, and
python-chess starts it with an argv list (``asyncio.subprocess_exec``), not a
shell string. The engine path and options are operator configuration, never
request data.

A one-time review can be forgotten; this test is the memory. It parses every
production module and fails on the two patterns that turn input into a command:

* ``shell=True`` in any call;
* ``os.system`` / ``os.popen``.

``subprocess.run([...])`` is deliberately **not** banned: an argv list is safe
and may be the right tool later. If that changes, the reviewer should see it in
a diff and update ``docs/safety-review.md`` rather than have a test fight it.

This is a tripwire, not a sandbox. It proves the code shape, not the behaviour of
a new feature; behavioural coverage lives in ``tests/test_api_analyze.py`` (fixed
cost) and ``tests/test_api_report.py`` (bound SQL).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _production_files() -> list[Path]:
    """Every module that can run in the service, excluding scripts and tests."""
    return sorted(
        [
            *(ROOT / "analyzer").rglob("*.py"),
            *(ROOT / "server").rglob("*.py"),
        ]
    )


def _shell_violations(path: Path, source: str) -> list[str]:
    """Calls that would hand a string to a shell, in one parsed module."""
    tree = ast.parse(source, filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        for keyword in node.keywords:
            if (
                keyword.arg == "shell"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
            ):
                violations.append(f"{path}: shell=True")

        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "os"
            and function.attr in {"system", "popen"}
        ):
            violations.append(f"{path}: os.{function.attr}()")

    return violations


def test_the_scanner_detects_the_patterns_it_claims_to_detect():
    """A tripwire that cannot trip is worse than none."""
    assert _shell_violations(
        Path("example.py"), "import os\nos.system('echo hi')\n"
    ) == ["example.py: os.system()"]
    assert _shell_violations(
        Path("example.py"), "import subprocess\nsubprocess.run('x', shell=True)\n"
    ) == ["example.py: shell=True"]


def test_no_production_code_asks_for_a_shell():
    violations: list[str] = []
    for path in _production_files():
        violations.extend(_shell_violations(path, path.read_text(encoding="utf-8")))

    assert not violations, (
        "production code must never use a shell; see docs/safety-review.md:\n"
        + "\n".join(violations)
    )
