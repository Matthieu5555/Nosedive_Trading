from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated

from algotrading.core.provenance import ProvenanceValidationError, validate_stamp
from annotated_types import Ge, Gt
from pydantic import AwareDatetime, ConfigDict, TypeAdapter, ValidationError

from .errors import ContractValidationError
from .registry import (
    datetime_field_names,
    numeric_field_names,
    optional_numeric_field_names,
    spec_for_table,
    table_for_contract,
)

_STRICT = ConfigDict(strict=True)
_POSITIVE_NUMBER: TypeAdapter[float] = TypeAdapter(Annotated[float, Gt(0)], config=_STRICT)
_NON_NEGATIVE_NUMBER: TypeAdapter[float] = TypeAdapter(Annotated[float, Ge(0)], config=_STRICT)
_AWARE_DATETIME: TypeAdapter[datetime] = TypeAdapter(AwareDatetime, config=_STRICT)


def _check_numeric(table: str, name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractValidationError(
            table, name, value, "must be a numeric int/float, not a string or other type"
        )
    if not math.isfinite(value):
        raise ContractValidationError(table, name, value, "must be finite (no NaN or inf)")


def _check_ohlc(table: str, record: object) -> None:
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
    spec = spec_for_table(table)

    for pk in spec.primary_key:
        if getattr(record, pk) is None:
            raise ContractValidationError(table, pk, None, "primary-key field must not be None")

    optional_numeric = set(optional_numeric_field_names(spec.contract))
    for name in numeric_field_names(spec.contract):
        value = getattr(record, name)
        if value is None and name in optional_numeric:
            continue
        _check_numeric(table, name, value)

    for name in spec.positive_fields:
        value = getattr(record, name)
        try:
            _POSITIVE_NUMBER.validate_python(value)
        except ValidationError:
            raise ContractValidationError(
                table, name, value, "must be strictly positive"
            ) from None

    for name in spec.non_negative_fields:
        value = getattr(record, name)
        try:
            _NON_NEGATIVE_NUMBER.validate_python(value)
        except ValidationError:
            raise ContractValidationError(table, name, value, "must be non-negative") from None

    for name in datetime_field_names(spec.contract):
        value = getattr(record, name)
        if value is None:
            continue
        try:
            _AWARE_DATETIME.validate_python(value)
        except ValidationError:
            raise ContractValidationError(
                table, name, value, "datetime must be timezone-aware, not naive"
            ) from None

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
        try:
            validate_stamp(prov)
        except ProvenanceValidationError as exc:
            raise ContractValidationError(
                table, "provenance", prov, f"provenance stamp is invalid: {exc.reason}"
            ) from exc


def validate(record: object) -> None:
    validate_record(table_for_contract(type(record)), record)
