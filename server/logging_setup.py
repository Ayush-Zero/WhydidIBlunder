"""Structured logging, with a request id that follows the whole request.

Logs are emitted as ``key=value`` pairs rather than prose, so they can be
grepped and counted without a parser. Two rules the rest of the code follows:

* **Never log a secret.** The API key for the explanation layer (Phase 5) lives
  in ``.env``; nothing in this codebase logs an environment value. ``/health``
  deliberately reports no configuration either.
* **Every request line carries a request id** (``req=...``) and a duration, so a
  slow endpoint and the errors it produced can be correlated afterwards.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

__all__ = ["configure_logging", "request_id_var"]

#: The current request's id. A ContextVar rather than an argument because logging
#: happens in places (libraries, error handlers) that never see the request.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

#: Attribute names the standard formatter already consumes; everything else on a
#: LogRecord is treated as a structured field.
_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


class KeyValueFormatter(logging.Formatter):
    """``asctime level logger req=<id> message key=value ...``"""

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record, '%Y-%m-%dT%H:%M:%S')} "
            f"{record.levelname:<7} {record.name} req={request_id_var.get()}"
        )
        message = record.getMessage()

        extras = " ".join(
            f"{key}={value!r}"
            for key, value in sorted(record.__dict__.items())
            if key not in _RESERVED and not key.startswith("_")
        )
        line = f"{base} {message}" + (f" {extras}" if extras else "")

        if record.exc_info:
            # The traceback goes on its own lines, after the grep-able summary.
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    """Configure the root logger once per process. Idempotent on purpose."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(KeyValueFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel((level or "INFO").upper())
    _CONFIGURED = True
