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


class SchemaCompatibilityError(StorageError):
    """A stored row has no value for a required (non-optional) contract field.

    Schema evolution here is additive-and-nullable only: a new column must be
    optional, so a partition written before it existed reads back with that field
    as ``None``. A *required* field coming back absent or null means the stored
    data no longer matches the contract — a removed or renamed column, or real type
    drift — so the read is refused rather than used to build an invalid instance
    (e.g. an ``IvPoint`` with ``k=None`` when ``k`` is a required float).
    """

    def __init__(self, contract: type, field: str) -> None:
        self.contract = contract
        self.field = field
        super().__init__(
            f"{contract.__name__!r}: required field {field!r} is absent or null in storage; "
            f"only Optional fields may be missing (additive-nullable schema-evolution rule)"
        )
