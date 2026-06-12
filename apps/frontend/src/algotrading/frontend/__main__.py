"""Run the frontend BFF with uvicorn."""

from __future__ import annotations

from pathlib import Path

import uvicorn

# Watch ONLY the source trees for auto-reload. Left unset, uvicorn watches the whole
# working directory recursively — including the Parquet store under data/ (hundreds of
# thousands of one-row files), which pins a core on the watcher and, after a mass write
# (a backfill), wedges the server entirely (observed live 2026-06-12: 93% CPU since
# boot, then /healthz timing out with a full accept backlog).
_REPO_ROOT = Path(__file__).resolve().parents[5]
_RELOAD_DIRS = [str(_REPO_ROOT / "apps"), str(_REPO_ROOT / "packages")]


def main() -> None:
    """Start a local development server."""

    uvicorn.run(
        "algotrading.frontend.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=_RELOAD_DIRS,
        # Bound the drain on reload: the default waits FOREVER for in-flight/keep-alive
        # connections, so a source edit while a browser holds a connection wedges the
        # restart and the API goes dark (observed live 2026-06-12, twice).
        timeout_graceful_shutdown=10,
    )


if __name__ == "__main__":
    main()
