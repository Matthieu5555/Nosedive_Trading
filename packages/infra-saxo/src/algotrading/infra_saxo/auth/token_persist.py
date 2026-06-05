"""Persist rotated Saxo tokens to a .env file across refreshes.

Saxo invalidates the previous refresh token on every refresh, so a long-running session must write
each rotation to disk to stay restart-resilient — otherwise a restart finds a refresh token Saxo has
already revoked. This wraps the pure :func:`upsert_env_vars` with the file I/O, kept separate so the
upsert stays trivially testable.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from algotrading.core.log import get_logger

from .env_tokens import upsert_env_vars

_log = get_logger(__name__)

_ACCESS_KEY = "SAXO_ACCESS_TOKEN"
_REFRESH_KEY = "SAXO_REFRESH_TOKEN"


def make_env_token_persister(env_path: Path) -> Callable[[str, str], None]:
    """Return an ``on_token_refresh`` hook that upserts rotated tokens into ``env_path``.

    When ``env_path`` does not exist, rotated tokens are kept in memory only (the hook logs and
    returns) — a deployment without a .env simply forgoes persistence rather than crashing.
    """

    def _persist(access: str, refresh: str) -> None:
        if not env_path.exists():
            _log.info("no .env at %s — rotated Saxo tokens kept in memory only", env_path)
            return
        env_path.write_text(
            upsert_env_vars(
                env_path.read_text(encoding="utf-8"),
                {_ACCESS_KEY: access, _REFRESH_KEY: refresh},
            ),
            encoding="utf-8",
        )

    return _persist
