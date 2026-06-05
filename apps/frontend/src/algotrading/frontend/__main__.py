"""Run the frontend BFF with uvicorn."""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Start a local development server."""

    uvicorn.run("algotrading.frontend.app:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    main()
