"""Application context: store wiring and injectable root resolution.

``AppContext`` holds the resolved ``ParquetStore``, the configs directory, and the
default underlying/window the routers fall back to. It is injectable so tests can
point it at a ``tmp_path`` store with fixture data; in production ``build()`` walks
up to the repo root and wires the canonical ``data/`` store.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from algotrading.core.config import ConfigError
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import enabled_indices, load_index_registry

# Repo-root marker: the canonical instructions file lives only at the workspace root.
_ROOT_MARKER = "AGENTS.md"

# The view fallback when a request names no index is resolved from the registry (the single
# source — never a hard-coded ticker), see `_default_index`. Empty when no registry is present,
# in which case the routers resolve to a labeled empty view rather than a stale single-name.
_DEFAULT_WINDOW_DAYS = 30


def _default_index(configs_dir: Path) -> str:
    """The registry's primary (first enabled) index — the view fallback when no index is given.

    Driven by the registry's ``enabled`` set, so it follows the config: with SPX parked it is
    SX5E, and it can never be a stale hand-set ticker. Returns ``""`` when no registry/enabled
    index is present (a fresh deployment, an empty test config) — the routers then fall back to
    an empty, labeled view instead of a hard-coded single-name.
    """
    try:
        registry = load_index_registry(configs_dir)
    except ConfigError:
        return ""
    enabled = enabled_indices(registry)
    return enabled[0].symbol if enabled else ""


class ContextError(Exception):
    """Raised when the workspace root cannot be resolved."""

    def __init__(self, start: Path) -> None:
        self.start = start
        super().__init__(
            f"could not locate workspace root from {start}: "
            f"no '{_ROOT_MARKER}' in the parent chain"
        )


def _find_repo_root(start: Path) -> Path:
    """Walk up from ``start`` until finding the directory holding the root marker."""
    for candidate in [start, *start.parents]:
        if (candidate / _ROOT_MARKER).exists():
            return candidate
    raise ContextError(start)


@dataclass(frozen=True, slots=True)
class AppContext:
    """Wired application context: the store, the configs directory, and defaults."""

    store_root: Path
    configs_dir: Path
    store: ParquetStore
    # The view fallback when a request names no index. Resolved from the registry by `build()`;
    # ``""`` for a direct construction that does not set it (an empty test context).
    default_underlying: str = ""
    default_window_days: int = _DEFAULT_WINDOW_DAYS

    @classmethod
    def build(
        cls,
        *,
        repo_root: Path | None = None,
        store_root: Path | None = None,
        default_underlying: str | None = None,
    ) -> AppContext:
        """Construct context from the repo root (or injected overrides for tests).

        ``store_root`` defaults to the ``ALGOTRADING_DATA_ROOT`` env var (the same override the
        capture/runner reads) when set, else ``<repo_root>/data``; ``configs_dir`` to
        ``<repo_root>/configs``. All are injectable so a test can wire a tmp store. The env
        override lets the front point at a separate demo/test store without touching the prod data.

        ``default_underlying`` defaults to the registry's primary enabled index (the single
        source) — never a hard-coded ticker; pass it explicitly only to override.
        """
        root = repo_root if repo_root is not None else _find_repo_root(Path(__file__).parent)
        configs_dir = root / "configs"
        if store_root is not None:
            resolved_store_root = store_root
        else:
            env_root = os.environ.get("ALGOTRADING_DATA_ROOT")
            resolved_store_root = Path(env_root) if env_root else root / "data"
        resolved_default = (
            default_underlying if default_underlying is not None else _default_index(configs_dir)
        )
        return cls(
            store_root=resolved_store_root,
            configs_dir=configs_dir,
            store=ParquetStore(resolved_store_root),
            default_underlying=resolved_default,
        )
