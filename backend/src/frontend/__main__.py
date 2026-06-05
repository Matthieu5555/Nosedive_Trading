"""Entry point: serve the BFF with uvicorn (``python -m frontend``)."""

from __future__ import annotations

import uvicorn

from .app import create_app


def main() -> None:
    """Run the dashboard BFF on localhost:8000."""
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
