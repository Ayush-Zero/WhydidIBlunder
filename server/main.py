"""``uvicorn`` entry point for the API process.

    .\\.venv\\Scripts\\python.exe -m server.main

Reads the database location and CORS origins from ``.env`` (see
``.env.example``). This is the only module that constructs the app for
production, so the app's configuration has exactly one home.
"""

from __future__ import annotations

import os

import uvicorn

from server.app import create_app

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
