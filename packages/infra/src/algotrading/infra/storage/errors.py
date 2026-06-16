from __future__ import annotations


class StorageError(Exception):
    pass


class AppendOnlyViolation(StorageError):

    def __init__(self, table: str, primary_key: tuple[object, ...]) -> None:
        self.table = table
        self.primary_key = primary_key
        super().__init__(
            f"append-only table {table!r}: a row already exists for primary key {primary_key!r}"
        )


class DuplicateKeyInBatch(StorageError):

    def __init__(self, table: str, primary_key: tuple[object, ...]) -> None:
        self.table = table
        self.primary_key = primary_key
        super().__init__(
            f"table {table!r}: primary key {primary_key!r} appears more than once in one write"
        )


class VersionedWriteNotAllowed(StorageError):

    def __init__(self, table: str, version: str) -> None:
        self.table = table
        self.version = version
        super().__init__(
            f"append-only table {table!r}: versioned writes (version={version!r}) are only "
            f"supported for derived tables"
        )


class SchemaCompatibilityError(StorageError):

    def __init__(self, contract: type, field: str) -> None:
        self.contract = contract
        self.field = field
        super().__init__(
            f"{contract.__name__!r}: required field {field!r} is absent or null in storage; "
            f"only Optional fields may be missing (additive-nullable schema-evolution rule)"
        )
