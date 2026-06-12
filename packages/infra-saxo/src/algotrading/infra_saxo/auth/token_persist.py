"""Persist rotated Saxo tokens to a .env file across refreshes.

Saxo invalidates the previous refresh token on every refresh, so a long-running session must write
each rotation to disk to stay restart-resilient — otherwise a restart finds a refresh token Saxo has
already revoked. The upsert itself is python-dotenv's ``set_key`` (replace-in-place, unrelated lines
untouched); what stays bespoke is the deployment rule: when no ``.env`` exists, rotated tokens are
kept in memory only rather than conjuring a file into existence.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from algotrading.core.log import get_logger
from dotenv import set_key

_log = get_logger(__name__)

_ACCESS_KEY = "SAXO_ACCESS_TOKEN"
_REFRESH_KEY = "SAXO_REFRESH_TOKEN"


def make_env_token_persister(env_path: Path) -> Callable[[str, str], None]:
    """Return an ``on_token_refresh`` hook that upserts rotated tokens into ``env_path``.

    When ``env_path`` does not exist, rotated tokens are kept in memory only (the hook logs and
    returns) — a deployment without a .env simply forgoes persistence rather than crashing.
    ``quote_mode="never"`` keeps the file format identical to a hand-written ``KEY=value`` .env.
    """

    def _persist(access: str, refresh: str) -> None:
        if not env_path.exists():
            _log.info("no .env at %s — rotated Saxo tokens kept in memory only", env_path)
            return
        set_key(env_path, _ACCESS_KEY, access, quote_mode="never")
        set_key(env_path, _REFRESH_KEY, refresh, quote_mode="never")

    return _persist
