"""Load the repo-root ``.env`` into ``os.environ`` so the BFF works the same
no matter how it is launched, by hand, by ``start.sh``, or by a systemd unit.

There is no ``python-dotenv`` dependency on purpose; this is a deliberately tiny
stdlib parser. It is intentionally permissive about what it ignores (blank lines,
comments, malformed lines) and strict about one thing: a variable already present
in the real process environment ALWAYS wins, so an explicit
``OPENROUTER_API_KEY=… uvicorn …`` on the command line is never clobbered by the
file. Quotes around a value are stripped; everything else is taken verbatim.
"""

from __future__ import annotations

import os
from pathlib import Path

# This file lives at apps/frontend/src/algotrading/frontend/envfile.py; the repo
# root is five parents up (frontend → algotrading → src → frontend → apps → root).
_REPO_ROOT = Path(__file__).resolve().parents[5]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def parse_env_text(text: str) -> dict[str, str]:
    """Parse ``.env`` text into a mapping. Blank lines, ``#`` comments, and lines
    without an ``=`` are skipped; surrounding quotes on the value are stripped."""
    parsed: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        parsed[key] = _unquote(value.strip())
    return parsed


def load_dotenv(path: Path | None = None, *, override: bool = False) -> None:
    """Read ``path`` (default: repo-root ``.env``) and set any variables that are
    not already in ``os.environ``. A missing file is a silent no-op. Set
    ``override=True`` only if you want the file to win over the real environment."""
    env_path = path if path is not None else _REPO_ROOT / ".env"
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for key, value in parse_env_text(text).items():
        if override or key not in os.environ:
            os.environ[key] = value
