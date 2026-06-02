"""DuckDB-over-Parquet storage adapters keyed to the typed contracts."""

from __future__ import annotations

from .adapter import ParquetStore, primary_key_of
from .errors import (
    AppendOnlyViolation,
    DuplicateKeyInBatch,
    SchemaCompatibilityError,
    StorageError,
)
from .schema import arrow_schema
from .serialization import from_row, to_row

__all__ = [
    "AppendOnlyViolation",
    "DuplicateKeyInBatch",
    "ParquetStore",
    "SchemaCompatibilityError",
    "StorageError",
    "arrow_schema",
    "from_row",
    "primary_key_of",
    "to_row",
]
