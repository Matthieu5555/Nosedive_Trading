"""Errors raised by the storage adapters.

Each one names the table and the offending key, so a rejected write tells an
operator exactly what collided rather than just "write failed".
"""

from __future__ import annotations


class StorageError(Exception):
    """Base class for all storage-layer failures."""


class AppendOnlyViolation(StorageError):
    """A write tried to overwrite an existing row in an append-only table.

    Raw observations are sacred: once written they are never changed. Attempting
    to write a row whose primary key already exists is a bug in the caller, not
    something to silently ignore.
    """

    def __init__(self, table: str, primary_key: tuple[object, ...]) -> None:
        self.table = table
        self.primary_key = primary_key
        super().__init__(
            f"append-only table {table!r}: a row already exists for primary key {primary_key!r}"
        )


class DuplicateKeyInBatch(StorageError):
    """A single write batch contained two rows with the same primary key."""

    def __init__(self, table: str, primary_key: tuple[object, ...]) -> None:
        self.table = table
        self.primary_key = primary_key
        super().__init__(
            f"table {table!r}: primary key {primary_key!r} appears more than once in one write"
        )
