from __future__ import annotations

from typing import Any


class ContractError(Exception):
    pass


class ContractValidationError(ContractError):

    def __init__(self, table: str, field: str, value: Any, reason: str) -> None:
        self.table = table
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"{table}.{field}={value!r}: {reason}")


class UnknownTableError(ContractError):

    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"no contract registered for table {table!r}")
