"""Write-ahead validation: every malformed shape is rejected, not coerced.

Each rejection case starts from a known-good baseline record and breaks exactly
one field, so the test isolates the single rule under examination. The happy-path
test proves all twelve baselines validate, so a false rejection would show up too.
"""

from __future__ import annotations

import dataclasses
import math
from datetime import datetime

import pytest

from contracts import ContractValidationError, validate_record
from fixtures import baseline_records

DERIVED_TABLES = (
    "forward_curve",
    "iv_points",
    "surface_parameters",
    "surface_grid",
    "pricing_results",
    "risk_aggregates",
    "scenario_results",
)


@pytest.mark.parametrize("table", sorted(baseline_records().keys()))
def test_baseline_records_validate(table: str) -> None:
    validate_record(table, baseline_records()[table])


def test_missing_primary_key_is_rejected() -> None:
    record = dataclasses.replace(baseline_records()["raw_market_events"], event_id=None)
    with pytest.raises(ContractValidationError) as info:
        validate_record("raw_market_events", record)
    assert info.value.field == "event_id"


def test_numeric_stored_as_decimal_string_is_rejected() -> None:
    # A number arriving as text is an error, not something to silently float().
    record = dataclasses.replace(baseline_records()["iv_points"], iv="0.2")
    with pytest.raises(ContractValidationError) as info:
        validate_record("iv_points", record)
    assert info.value.field == "iv"


def test_naive_datetime_is_rejected() -> None:
    naive = datetime(2026, 5, 29, 15, 30)  # no tzinfo
    record = dataclasses.replace(baseline_records()["market_state_snapshots"], snapshot_ts=naive)
    with pytest.raises(ContractValidationError) as info:
        validate_record("market_state_snapshots", record)
    assert info.value.field == "snapshot_ts"


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nan_or_inf_numeric_is_rejected(bad: float) -> None:
    record = dataclasses.replace(baseline_records()["forward_curve"], forward=bad)
    with pytest.raises(ContractValidationError) as info:
        validate_record("forward_curve", record)
    assert info.value.field == "forward"


def test_negative_value_where_positivity_required_is_rejected() -> None:
    record = dataclasses.replace(baseline_records()["forward_curve"], forward=-1.0)
    with pytest.raises(ContractValidationError) as info:
        validate_record("forward_curve", record)
    assert info.value.field == "forward"


@pytest.mark.parametrize("table", DERIVED_TABLES)
def test_derived_record_without_source_snapshot_ts_is_rejected(table: str) -> None:
    record = dataclasses.replace(baseline_records()[table], source_snapshot_ts=None)
    with pytest.raises(ContractValidationError) as info:
        validate_record(table, record)
    assert info.value.field == "source_snapshot_ts"


@pytest.mark.parametrize("table", DERIVED_TABLES)
def test_derived_record_without_provenance_is_rejected(table: str) -> None:
    record = dataclasses.replace(baseline_records()[table], provenance=None)
    with pytest.raises(ContractValidationError) as info:
        validate_record(table, record)
    assert info.value.field == "provenance"
