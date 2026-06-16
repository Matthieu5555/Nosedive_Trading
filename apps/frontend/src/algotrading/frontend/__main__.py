from __future__ import annotations

from pathlib import Path

import uvicorn

_REPO_ROOT = Path(__file__).resolve().parents[5]
_RELOAD_DIRS = [str(_REPO_ROOT / "apps"), str(_REPO_ROOT / "packages")]


def main() -> None:

    uvicorn.run(
        "algotrading.frontend.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=_RELOAD_DIRS,
        timeout_graceful_shutdown=10,
    )


if __name__ == "__main__":
    main()
