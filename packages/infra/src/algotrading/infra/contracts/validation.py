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

from algotrading.core.provenance import ProvenanceValidationError, validate_stamp

from .errors import ContractValidationError
from .registry import (
    datetime_field_names,
    numeric_field_names,
    optional_numeric_field_names,
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


def _check_ohlc(table: str, record: object) -> None:
    """Reject an OHLC bar whose extremes are inconsistent (e.g. high < low).

    A daily bar's ``high`` must be the day's max and ``low`` its min, so every other
    price must sit within ``[low, high]``. A bar with ``high < low`` (or an open/close
    outside the range) is corrupt input, not something to coerce — it is rejected with
    the offending field named, so a bad fetch fails at the write door.
    """
    high = record.high  # type: ignore[attr-defined]
    low = record.low  # type: ignore[attr-defined]
    if high < low:
        raise ContractValidationError(
            table, "high", high, f"high must be >= low ({low!r})"
        )
    for name in ("open", "close"):
        value = getattr(record, name)
        if not (low <= value <= high):
            raise ContractValidationError(
                table, name, value, f"must lie within [low={low!r}, high={high!r}]"
            )


def validate_record(table: str, record: object) -> None:
    """Validate one record against its table contract. Raise on the first failure.

    Returns ``None`` when the record is valid; raises
    :class:`ContractValidationError` otherwise.
    """
    spec = spec_for_table(table)

    for pk in spec.primary_key:
        if getattr(record, pk) is None:
            raise ContractValidationError(table, pk, None, "primary-key field must not be None")

    optional_numeric = set(optional_numeric_field_names(spec.contract))
    for name in numeric_field_names(spec.contract):
        value = getattr(record, name)
        # An Optional numeric field may be None (an additive-nullable field absent on an
        # older partition); a non-None value is still range-checked.
        if value is None and name in optional_numeric:
            continue
        _check_numeric(table, name, value)

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

    if table == "daily_bar":
        _check_ohlc(table, record)

    if spec.requires_provenance:
        prov = getattr(record, "provenance", None)
        if prov is None:
            raise ContractValidationError(
                table, "provenance", None, "derived record must carry a provenance stamp"
            )
        # The stamp's own wellformedness — tz-aware timestamps, non-empty version
        # and config fields, and a hash that matches its contents — is owned by the
        # provenance module. Delegate to it so the rule lives once, and surface any
        # failure as the contract-layer error the write path already expects.
        try:
            validate_stamp(prov)
        except ProvenanceValidationError as exc:
            raise ContractValidationError(
                table, "provenance", prov, f"provenance stamp is invalid: {exc.reason}"
            ) from exc


def validate(record: object) -> None:
    """Validate a record, looking up its table from its contract class."""
    validate_record(table_for_contract(type(record)), record)
