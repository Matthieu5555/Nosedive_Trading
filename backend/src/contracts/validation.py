"""Write-ahead validation: reject a malformed record before it is ever stored.

This is the enforcement point for the contract rules. The storage adapter runs it
on every record before a write, and it is callable on its own so a producer can
check a record early. It rejects rather than coerces: a number arriving as a
string is an error, not something to quietly ``float()``. Each rejection names the
table, the field, and the offending value, so the log says exactly what was wrong.

The checks, in order: primary-key fields present; numerics are real finite
numbers (not strings, not NaN/inf, not bools); positivity/non-negativity where the
registry requires it; datetimes timezone-aware; derived records carry a
``source_snapshot_ts`` back-reference and a well-formed provenance stamp.
"""

from __future__ import annotations

import math

from .errors import ContractValidationError
from .registry import (
    datetime_field_names,
    numeric_field_names,
    spec_for_table,
    table_for_contract,
)


def _check_numeric(table: str, name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractValidationError(
            table, name, value, "must be a numeric int/float, not a string or other type"
        )
    if not math.isfinite(value):
        raise ContractValidationError(table, name, value, "must be finite (no NaN or inf)")


def validate_record(table: str, record: object) -> None:
    """Validate one record against its table contract. Raise on the first failure.

    Returns ``None`` when the record is valid; raises
    :class:`ContractValidationError` otherwise.
    """
    spec = spec_for_table(table)

    for pk in spec.primary_key:
        if getattr(record, pk) is None:
            raise ContractValidationError(table, pk, None, "primary-key field must not be None")

    for name in numeric_field_names(spec.contract):
        _check_numeric(table, name, getattr(record, name))

    for name in spec.positive_fields:
        value = getattr(record, name)
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0):
            raise ContractValidationError(table, name, value, "must be strictly positive")

    for name in spec.non_negative_fields:
        value = getattr(record, name)
        if not (isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0):
            raise ContractValidationError(table, name, value, "must be non-negative")

    for name in datetime_field_names(spec.contract):
        value = getattr(record, name)
        if value is None:
            continue
        if value.tzinfo is None:
            raise ContractValidationError(
                table, name, value, "datetime must be timezone-aware, not naive"
            )

    if spec.requires_source_snapshot_ts and getattr(record, "source_snapshot_ts", None) is None:
        raise ContractValidationError(
            table,
            "source_snapshot_ts",
            None,
            "derived record must reference the source snapshot_ts it was computed from",
        )

    if spec.requires_provenance:
        prov = getattr(record, "provenance", None)
        if prov is None:
            raise ContractValidationError(
                table, "provenance", None, "derived record must carry a provenance stamp"
            )
        if not getattr(prov, "config_hash", ""):
            raise ContractValidationError(
                table, "provenance", prov, "provenance stamp must carry a config_hash"
            )
        if not getattr(prov, "code_version", ""):
            raise ContractValidationError(
                table, "provenance", prov, "provenance stamp must carry a code_version"
            )


def validate(record: object) -> None:
    """Validate a record, looking up its table from its contract class."""
    validate_record(table_for_contract(type(record)), record)
