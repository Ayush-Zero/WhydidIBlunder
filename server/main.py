"""``uvicorn`` entry point for the API process.

    .\\.venv\\Scripts\\python.exe -m server.main

Reads the database location, CORS origins and rate-limit settings from ``.env``
(see ``.env.example``). This is the only module that constructs the app for
production, so the app's configuration has exactly one home.
"""

from __future__ import annotations

import os

import uvicorn
from dotenv import load_dotenv

from server.app import create_app

# Load `.env` before the app reads ``DB_PATH``, ``CHESSCOM_CACHE_DIR``,
# ``CORS_ORIGINS`` and the listen address. The worker gets this transitively via
# ``analyzer.config``; the API does not import the analyser, so it must load the
# file itself or every server setting would be ignored.
load_dotenv()

#: ``uvicorn server.main:app`` works as well as ``python -m server.main``.
app = create_app()


def main() -> None:
    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
        log_config=None,  # our key=value logging already configures the root logger
    )


if __name__ == "__main__":
    main()
