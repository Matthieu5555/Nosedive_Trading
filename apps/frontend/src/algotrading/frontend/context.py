from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from algotrading.core.config import ConfigError, ConfigFieldError
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import enabled_indices, load_index_registry

_ROOT_MARKER = "AGENTS.md"

_DEFAULT_WINDOW_DAYS = 30


def _default_index(configs_dir: Path) -> str:
    try:
        registry = load_index_registry(configs_dir)
    except (ConfigError, ConfigFieldError):
        return ""
    enabled = enabled_indices(registry)
    return enabled[0].symbol if enabled else ""


class ContextError(Exception):

    def __init__(self, start: Path) -> None:
        self.start = start
        super().__init__(
            f"could not locate workspace root from {start}: "
            f"no '{_ROOT_MARKER}' in the parent chain"
        )


def _find_repo_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / _ROOT_MARKER).exists():
            return candidate
    raise ContextError(start)


@dataclass(frozen=True, slots=True)
class AppContext:

    store_root: Path
    configs_dir: Path
    store: ParquetStore
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
