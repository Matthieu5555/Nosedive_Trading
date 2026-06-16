from __future__ import annotations

from pathlib import Path

from algotrading.infra.storage import ParquetStore


def store_root(store: object) -> Path:
    if isinstance(store, ParquetStore):
        return Path(store.root)
    if isinstance(store, (str, Path)):
        return Path(store)
    raise TypeError(f"cannot resolve a storage root from {type(store).__name__}")
