"""Application context: store wiring and injectable root resolution.

``AppContext`` holds the resolved ``ParquetStore``, the configs directory, and the
default underlying/window the routers fall back to. It is injectable so tests can
point it at a ``tmp_path`` store with fixture data; in production ``build()`` walks
up to the repo root and wires the canonical ``data/`` store.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from algotrading.infra.storage import ParquetStore

# Repo-root marker: the canonical instructions file lives only at the workspace root.
_ROOT_MARKER = "AGENTS.md"

# Fallbacks when no config names a default.
_DEFAULT_UNDERLYING = "AAPL"
_DEFAULT_WINDOW_DAYS = 30


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
    default_underlying: str = _DEFAULT_UNDERLYING
    default_window_days: int = _DEFAULT_WINDOW_DAYS

    @classmethod
    def build(
        cls,
        *,
        repo_root: Path | None = None,
        store_root: Path | None = None,
        default_underlying: str = _DEFAULT_UNDERLYING,
    ) -> AppContext:
        """Construct context from the repo root (or injected overrides for tests).

        ``store_root`` defaults to ``<repo_root>/data``; ``configs_dir`` to
        ``<repo_root>/configs``. Both are injectable so a test can wire a tmp store.
        """
        root = repo_root if repo_root is not None else _find_repo_root(Path(__file__).parent)
        resolved_store_root = store_root if store_root is not None else root / "data"
        return cls(
            store_root=resolved_store_root,
            configs_dir=root / "configs",
            store=ParquetStore(resolved_store_root),
            default_underlying=default_underlying,
        )
