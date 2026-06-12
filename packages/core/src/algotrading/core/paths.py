"""One home for workspace-anchored paths and the entrypoint ``.env`` loader.

Before this module, every entrypoint re-derived the repo root with its own
``Path(__file__).resolve().parents[N]`` (nine sites, four distinct N — a file move
silently re-points any of them), the ``ALGOTRADING_DATA_ROOT`` default was spelled in
three places, and two private ``.env`` parsers had drifted apart in quote handling
(2026-06 maintainability audit, M23). This is the single seam: the root is anchored
once, the data-root default is spelled once, and ``.env`` loading delegates to
python-dotenv (the maintained standard) instead of a hand-rolled parser.

``load_env_file`` keeps the documented precedence: a variable already set in the real
environment **wins** over the file (``override=False``), so a systemd
``EnvironmentFile`` or an explicit shell export is never shadowed by a stale ``.env``.
A missing file is a clean no-op. ``${VAR}`` interpolation is disabled
(``interpolate=False``) so credential values load byte-for-byte as written, matching
the retired parsers.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

__all__ = ["DATA_ROOT_ENV_VAR", "data_root", "load_env_file", "repo_root"]

# The env var that relocates the canonical parquet store, honored identically by the
# EOD runner, the BFF, and the operator scripts.
DATA_ROOT_ENV_VAR = "ALGOTRADING_DATA_ROOT"

# Anchored once: this file lives at packages/core/src/algotrading/core/paths.py, five
# levels below the workspace root. The uv workspace installs every member editable, so
# the package always runs from this checkout and the anchor cannot dangle.
_REPO_ROOT = Path(__file__).resolve().parents[5]


def repo_root() -> Path:
    """The workspace root — the directory holding ``pyproject.toml``, ``configs/``, ``data/``."""
    return _REPO_ROOT


def data_root() -> Path:
    """The canonical store root: ``$ALGOTRADING_DATA_ROOT``, defaulting to ``<repo>/data``.

    An empty env var counts as unset (falls back to the default), so an accidental
    ``ALGOTRADING_DATA_ROOT=`` in a unit file cannot point the store at ``Path("")``.
    """
    override = os.environ.get(DATA_ROOT_ENV_VAR)
    return Path(override) if override else _REPO_ROOT / "data"


def load_env_file(path: str | Path | None = None) -> bool:
    """Load ``KEY=VALUE`` pairs from ``path`` (default ``<repo>/.env``) into ``os.environ``.

    Already-set variables win (``override=False``); values load literally (no ``${VAR}``
    interpolation). Returns ``True`` when the file was found and parsed, ``False`` for a
    missing file (a clean no-op, so a non-credentialed environment stays a no-op
    downstream).
    """
    file = Path(path) if path is not None else _REPO_ROOT / ".env"
    return load_dotenv(file, override=False, interpolate=False)
