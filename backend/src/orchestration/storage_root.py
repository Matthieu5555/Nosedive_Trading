"""Resolve the filesystem root the run-state ledger lives under, from a store.

The run-state ledger is operational bookkeeping kept beside the data, under the same
root the :class:`storage.ParquetStore` owns. The pipeline takes the store as its
storage dependency but only needs that root to place the ledger, so this is the one
small adapter that reads it — kept in its own module so the dependency on the store's
shape is named and isolated rather than reached into ad hoc.
"""

from __future__ import annotations

from pathlib import Path

from storage import ParquetStore


def store_root(store: object) -> Path:
    """Return the data root a store writes under, as a :class:`Path`.

    Accepts a :class:`storage.ParquetStore` (reads its ``root``) or a bare path-like;
    the pipeline passes whatever store it was handed and gets back the directory the
    ledger belongs in. Raising on an unrecognized object is deliberate — a store with
    no resolvable root is a wiring bug, not something to paper over with a default.
    """
    if isinstance(store, ParquetStore):
        return Path(store.root)
    if isinstance(store, (str, Path)):
        return Path(store)
    raise TypeError(f"cannot resolve a storage root from {type(store).__name__}")
