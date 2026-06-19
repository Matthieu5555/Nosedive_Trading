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


class StaleRunError(StorageError):
    """A specific capture ``run_id`` was requested, but its data is not the one
    currently persisted for that ``trade_date``.

    The parquet store keeps only the latest capture's data per ``trade_date``
    partition (overwrite-last-wins). Re-fetching a day replaces the day's data in
    place, so an older ``run_id`` is addressable as an identity (it still appears
    in the run ledger) but its rows are gone. Read with ``run_id=None`` to get the
    latest, or pass the latest ``run_id``.
    """

    def __init__(
        self, table: str, trade_date: object, run_id: str, latest: str | None
    ) -> None:
        self.table = table
        self.trade_date = trade_date
        self.run_id = run_id
        self.latest = latest
        super().__init__(
            f"table {table!r}, trade_date {trade_date}: run_id {run_id!r} is not the "
            f"persisted capture (latest is {latest!r}); its data was overwritten. "
            f"Read run_id=None for the latest, or pass {latest!r}."
        )


class SchemaCompatibilityError(StorageError):

    def __init__(self, contract: type, field: str) -> None:
        self.contract = contract
        self.field = field
        super().__init__(
            f"{contract.__name__!r}: required field {field!r} is absent or null in storage; "
            f"only Optional fields may be missing (additive-nullable schema-evolution rule)"
        )
