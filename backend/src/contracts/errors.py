"""Errors raised when a record does not satisfy a contract.

Every error carries the value that triggered it, so a rejection log says exactly
which field of which record was wrong rather than just "validation failed".
"""

from __future__ import annotations

from typing import Any


class ContractError(Exception):
    """Base class for all contract-layer failures."""


class ContractValidationError(ContractError):
    """A record violated a field-level rule of its table contract.

    Attributes:
        table: the table family the record belongs to (e.g. ``"iv_points"``).
        field: the offending field name.
        value: the actual value that failed the check, kept as evidence.
        reason: a plain-language description of the rule that was broken.
    """

    def __init__(self, table: str, field: str, value: Any, reason: str) -> None:
        self.table = table
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"{table}.{field}={value!r}: {reason}")


class UnknownTableError(ContractError):
    """A table name was used that is not in the contract registry."""

    def __init__(self, table: str) -> None:
        self.table = table
        super().__init__(f"no contract registered for table {table!r}")
