"""Load a ``.env`` file into the process environment at an entrypoint (ops plumbing).

The credential boundary is the environment: ``credentials_present`` / ``load_lst_consumer`` and
the config loaders read ``os.environ``. Nothing in the runtime auto-loads ``.env`` — ``uv run``
does not, and the systemd unit has no ``EnvironmentFile`` by default — so an entrypoint that needs
``.env`` values (the EOD capture, the OHLC backfill) must load it itself, once, before it builds
anything. This is that loader: a tiny, dependency-free ``KEY=VALUE`` reader, not a config parser.

Precedence is the standard one: a variable already set in the real environment **wins** over the
file (``override=False``), so a systemd ``EnvironmentFile`` / an explicit shell export is never
shadowed by a stale ``.env``. Lines that are blank or start with ``#`` are skipped; an optional
``export`` prefix is stripped; a value wrapped in matching single/double quotes is unwrapped. A
missing file is a no-op (returns 0) — a non-credentialed environment stays a clean no-op downstream.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

__all__ = ["load_env_file"]


def _unwrap(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_env_file(
    path: str | Path,
    *,
    environ: MutableMapping[str, str] | None = None,
    override: bool = False,
) -> int:
    """Load ``KEY=VALUE`` pairs from ``path`` into ``environ`` (default ``os.environ``).

    Returns the number of variables set. A missing file returns 0 (a clean no-op). A line without
    ``=`` is skipped (not an error — a partial/edited file should still load what it can). With
    ``override=False`` (the default) a key already present in ``environ`` is left untouched, so the
    real environment outranks the file.
    """
    target: MutableMapping[str, str] = os.environ if environ is None else environ
    file = Path(path)
    if not file.is_file():
        return 0

    loaded = 0
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        if not override and key in target:
            continue
        target[key] = _unwrap(value)
        loaded += 1
    return loaded
