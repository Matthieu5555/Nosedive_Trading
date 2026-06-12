"""Golden equivalence pins for the contracts plane — the frozen-seam gate.

These pins were captured from the pre-pydantic (hand-rolled) implementation on
2026-06-12, BEFORE the owner-ruled pydantic-v2 internals swap of ``validation.py``,
``serialization.py`` and ``json_io.py``. They are the proof that the swap moves no
persisted byte and changes no write-door decision. What is pinned:

* **Storage-row bytes** — for every table in the registry, ``to_row`` of one valid
  baseline record must reproduce the committed fixture exactly; the JSON columns
  are the persisted bytes (the byte-identical-replay / canonical-hash anchor).
* **Round trip** — ``from_row(to_row(record)) == record`` for every table.
* **Write-door accept/reject** — the exact cases ``validate_record`` rejects today
  (bool-as-numeric, numpy ints/bools, non-finite floats, naive datetimes, ...) and,
  just as importantly, its *scope*: numeric/positivity/tz rules only — a ``None``
  in an unchecked ``str`` field passes (no silent widening to full-record strictness).
* **The Optional schema-evolution rule** — absent-or-null is forgiven only for an
  ``Optional`` field, at both the top-level-column and nested-JSON-bundle levels.
* **The raw-event JSON sidecar bytes** (``json_io``) — exact text, ``__dec__``
  Decimal wrapper, and the read-side ``provider`` default.

Expected values are derived from the contract docstrings and the storage format
spec (flat scalars + sorted-key compact JSON columns, UTC-normalized ISO
timestamps), not copied from the implementation.

Regenerating the fixtures is a deliberate persisted-format change, never a way to
make this gate pass:

    uv run python packages/infra/tests/test_contracts_plane_golden.py --regenerate
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.infra.contracts import ContractValidationError, validate_record
from algotrading.infra.contracts.bundles import SurfaceFitDiagnostics
from algotrading.infra.contracts.registry import REGISTRY
from algotrading.infra.contracts.tables import BasketLeg, PricingResult, RiskAggregate
from algotrading.infra.storage.errors import SchemaCompatibilityError
from algotrading.infra.storage.events import CollectorEvent
from algotrading.infra.storage.json_io import events_from_json, events_to_json
from algotrading.infra.storage.serialization import from_row, to_row
from fixtures.records import CALC_TS, SNAPSHOT_TS, baseline_records, make_stamp

_GOLDEN_DIR = Path(__file__).parent / "golden"
_ROWS_FIXTURE = _GOLDEN_DIR / "contracts_plane_rows.json"
_EVENTS_FIXTURE = _GOLDEN_DIR / "collector_events.json"

# A non-UTC zone exercises the codec's normalize-to-UTC rule for the datetimes
# inside a JSON column (nested stamp timestamps must persist as +00:00 ISO).
_CEST = timezone(timedelta(hours=2))

# The compact-JSON-column byte convention the storage format pins (sorted keys,
# no whitespace) — restated here independently so a drift in the codec's dump
# arguments fails the fixture comparison.
_COLUMN_DUMP_ARGS: dict[str, Any] = {"sort_keys": True, "separators": (",", ":")}


def _non_utc_stamp() -> ProvenanceStamp:
    """A valid stamp whose timestamps carry a +02:00 offset (not UTC)."""
    return stamp(
        calc_ts=CALC_TS.astimezone(_CEST),
        code_version="0.1.0-fixture",
        config_hashes={"cfg": "cfg-hash-0"},
        source_records=(source_ref("raw_market_events", "sess-1", "evt-1"),),
        source_timestamps=(SNAPSHOT_TS.astimezone(_CEST),),
    )


def _non_utc_record() -> RiskAggregate:
    """A derived record stamped with non-UTC timestamps, for the byte pin."""
    return dataclasses.replace(
        baseline_records()["risk_aggregates"], provenance=_non_utc_stamp()
    )


def _fixture_value(value: object) -> object:
    """Encode one storage-row value losslessly for the JSON fixture file."""
    # datetime first: it is a date subclass, and the tag must record which it was.
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    return value


def _encode_row(row: dict[str, Any]) -> dict[str, Any]:
    return {name: _fixture_value(value) for name, value in row.items()}


def _all_rows() -> dict[str, dict[str, Any]]:
    return {
        table: _encode_row(to_row(REGISTRY[table].contract, record))
        for table, record in baseline_records().items()
    }


def _collector_events() -> list[CollectorEvent]:
    return [
        CollectorEvent(
            collector_session_id="sess-1",
            event_id="evt-1",
            receipt_ts=datetime(2026, 5, 29, 15, 30, 0, tzinfo=UTC),
            instrument_key="AAPL|STK|SMART|USD|1|u-AAPL|||",
            field_name="bid",
            field_value=Decimal("4999.50"),
            underlying="AAPL",
            provider="DERIBIT",
            exchange_ts=datetime(2026, 5, 29, 15, 29, 59, tzinfo=UTC),
            contract_id_broker="c-1",
        ),
        CollectorEvent(
            collector_session_id="sess-1",
            event_id="evt-2",
            receipt_ts=datetime(2026, 5, 29, 15, 30, 1, tzinfo=UTC),
            instrument_key="AAPL|STK|SMART|USD|1|u-AAPL|||",
            field_name="ask",
            field_value=None,
            underlying="AAPL",
            provider="SAXO",
        ),
        CollectorEvent(
            collector_session_id="sess-1",
            event_id="evt-3",
            receipt_ts=datetime(2026, 5, 29, 15, 30, 2, tzinfo=UTC),
            instrument_key="AAPL|STK|SMART|USD|1|u-AAPL|||",
            field_name="market_state",
            field_value="open",
            underlying="AAPL",
        ),
    ]


def _load_rows_fixture() -> dict[str, Any]:
    return json.loads(_ROWS_FIXTURE.read_text())


# --- coverage: the pins span the whole registry ------------------------------


def test_baseline_records_cover_every_registered_table() -> None:
    assert set(baseline_records()) == set(REGISTRY)


def test_every_positivity_field_is_a_numeric_field() -> None:
    """Registry sanity the rejection ordering relies on.

    The numeric finiteness/type check runs before the positivity checks, so a
    positivity-listed field must be numeric-annotated — otherwise a sign check
    could see a value the type gate never inspected.
    """
    from algotrading.infra.contracts.registry import numeric_field_names

    for spec in REGISTRY.values():
        numeric = set(numeric_field_names(spec.contract))
        assert set(spec.positive_fields) <= numeric, spec.name
        assert set(spec.non_negative_fields) <= numeric, spec.name


# --- persisted bytes: to_row against committed fixtures ----------------------


@pytest.mark.parametrize("table", sorted(REGISTRY))
def test_storage_row_matches_the_committed_golden_fixture(table: str) -> None:
    record = baseline_records()[table]
    row = to_row(REGISTRY[table].contract, record)
    expected = _load_rows_fixture()["rows"][table]
    assert _encode_row(row) == expected


@pytest.mark.parametrize("table", sorted(REGISTRY))
def test_row_round_trips_to_an_equal_record(table: str) -> None:
    record = baseline_records()[table]
    contract = REGISTRY[table].contract
    assert from_row(contract, to_row(contract, record)) == record


def test_json_columns_are_compact_sorted_json() -> None:
    # The JSON-column byte convention itself: re-dumping the parsed column with
    # the pinned arguments must reproduce the stored string exactly.
    for table, record in baseline_records().items():
        row = to_row(REGISTRY[table].contract, record)
        for name, value in row.items():
            if isinstance(value, str) and name in ("provenance", "diagnostics", "legs",
                                                   "flags", "instrument"):
                assert value == json.dumps(json.loads(value), **_COLUMN_DUMP_ARGS), (
                    table, name)


def test_non_utc_nested_timestamps_persist_normalized_to_utc() -> None:
    record = _non_utc_record()
    row = to_row(REGISTRY["risk_aggregates"].contract, record)
    expected = _load_rows_fixture()["non_utc_provenance_column"]
    assert row["provenance"] == expected
    assert "+02:00" not in row["provenance"]
    assert "+00:00" in row["provenance"]
    # and the offset-carrying original still reads back equal (same instant)
    assert from_row(REGISTRY["risk_aggregates"].contract, row) == record


# --- write door: exact accept behavior ---------------------------------------


@pytest.mark.parametrize("table", sorted(REGISTRY))
def test_every_baseline_record_passes_the_write_door(table: str) -> None:
    validate_record(table, baseline_records()[table])  # no raise == accepted


def test_optional_numeric_fields_accept_none() -> None:
    # additive-nullable: dollar_theta/dollar_rho are Optional numerics.
    record = baseline_records()["pricing_results"]
    record = dataclasses.replace(record, dollar_theta=None, dollar_rho=None)
    validate_record("pricing_results", record)


@pytest.mark.parametrize(
    ("case", "value"),
    [
        ("python int in a float field", 190),
        ("numpy float64 (a float subclass)", np.float64(190.4)),
    ],
)
def test_numeric_check_accepts_real_numbers_of_either_python_kind(
    case: str, value: object
) -> None:
    record = dataclasses.replace(baseline_records()["market_state_snapshots"], bid=value)
    validate_record("market_state_snapshots", record)


def test_validation_scope_is_the_checked_fields_only_not_full_record_strictness() -> None:
    """The write door checks numerics/positivity/tz/pk/provenance — nothing else.

    A ``None`` in a non-primary-key ``str`` field is not validation's business
    today; the pydantic swap must not silently widen the door to full-record
    strict validation.
    """
    record = baseline_records()["market_state_snapshots"]
    loose = dataclasses.replace(record, reference_type=None, underlying=None)
    validate_record("market_state_snapshots", loose)  # no raise == scope preserved


# --- write door: exact reject behavior ---------------------------------------

_NUMERIC_TYPE_REASON = "must be a numeric int/float, not a string or other type"
_FINITE_REASON = "must be finite (no NaN or inf)"


def _reject_cases() -> list[tuple[str, str, dict[str, Any], str, str]]:
    """(case id, table, field replacements, expected field, expected reason)."""
    return [
        ("bool is not numeric", "market_state_snapshots",
         {"bid": True}, "bid", _NUMERIC_TYPE_REASON),
        ("numpy bool is not numeric", "market_state_snapshots",
         {"bid": np.bool_(True)}, "bid", _NUMERIC_TYPE_REASON),
        ("numpy int64 is not a python number", "market_state_snapshots",
         {"bid": np.int64(190)}, "bid", _NUMERIC_TYPE_REASON),
        ("decimal-string is not numeric", "market_state_snapshots",
         {"bid": "190.4"}, "bid", _NUMERIC_TYPE_REASON),
        ("NaN is not finite", "market_state_snapshots",
         {"bid": float("nan")}, "bid", _FINITE_REASON),
        ("inf is not finite", "market_state_snapshots",
         {"bid": float("inf")}, "bid", _FINITE_REASON),
        ("optional numeric is still range-checked when present", "pricing_results",
         {"dollar_theta": float("nan")}, "dollar_theta", _FINITE_REASON),
        ("naive datetime", "market_state_snapshots",
         {"snapshot_ts": datetime(2026, 5, 29, 15, 30)}, "snapshot_ts",
         "datetime must be timezone-aware, not naive"),
        ("primary-key field None", "market_state_snapshots",
         {"instrument_key": None}, "instrument_key",
         "primary-key field must not be None"),
        ("zero where strictly positive required", "market_state_snapshots",
         {"reference_spot": 0.0}, "reference_spot", "must be strictly positive"),
        ("negative where non-negative required", "market_state_snapshots",
         {"bid": -0.5}, "bid", "must be non-negative"),
        # ordering pins: the numeric type/finiteness gate fires before the
        # positivity and tz checks (category-major, in declaration order).
        ("inf in a positive field reports the finiteness rule first",
         "market_state_snapshots",
         {"reference_spot": float("inf")}, "reference_spot", _FINITE_REASON),
        ("bool in a positive field reports the numeric-type rule first",
         "market_state_snapshots",
         {"reference_spot": True}, "reference_spot", _NUMERIC_TYPE_REASON),
        ("numeric failure wins over a later-category naive datetime",
         "market_state_snapshots",
         {"snapshot_ts": datetime(2026, 5, 29, 15, 30), "bid": float("nan")},
         "bid", _FINITE_REASON),
        # OHLC consistency (daily_bar only); low is 188.5, high 191.5 in the baseline.
        ("ohlc high below low", "daily_bar",
         {"high": 187.0}, "high", "high must be >= low (188.5)"),
        ("ohlc open outside the range", "daily_bar",
         {"open": 200.0}, "open", "must lie within [low=188.5, high=191.5]"),
        # derived-record lineage rules
        ("missing source_snapshot_ts back-reference", "forward_curve",
         {"source_snapshot_ts": None}, "source_snapshot_ts",
         "derived record must reference the source snapshot_ts it was computed from"),
        ("missing provenance stamp", "forward_curve",
         {"provenance": None}, "provenance",
         "derived record must carry a provenance stamp"),
    ]


@pytest.mark.parametrize(
    ("table", "replacements", "field", "reason"),
    [case[1:] for case in _reject_cases()],
    ids=[case[0] for case in _reject_cases()],
)
def test_write_door_rejects_with_the_exact_table_field_and_reason(
    table: str, replacements: dict[str, Any], field: str, reason: str
) -> None:
    record = dataclasses.replace(baseline_records()[table], **replacements)
    with pytest.raises(ContractValidationError) as exc:
        validate_record(table, record)
    assert exc.value.table == table
    assert exc.value.field == field
    assert exc.value.reason == reason


def test_tampered_provenance_stamp_is_rejected_as_a_contract_error() -> None:
    bad_stamp = dataclasses.replace(make_stamp(), stamp_hash="0" * 64)
    record = dataclasses.replace(baseline_records()["forward_curve"], provenance=bad_stamp)
    with pytest.raises(ContractValidationError) as exc:
        validate_record("forward_curve", record)
    assert exc.value.field == "provenance"
    assert exc.value.reason.startswith("provenance stamp is invalid:")


# --- the Optional schema-evolution rule, exactly ------------------------------


def _pricing_row() -> dict[str, Any]:
    return to_row(PricingResult, baseline_records()["pricing_results"])


def test_absent_optional_column_reads_back_as_none() -> None:
    row = _pricing_row()
    del row["dollar_theta"]
    restored = from_row(PricingResult, row)
    assert restored.dollar_theta is None  # type: ignore[attr-defined]


def test_null_optional_column_reads_back_as_none() -> None:
    row = _pricing_row()
    row["dollar_rho"] = None
    restored = from_row(PricingResult, row)
    assert restored.dollar_rho is None  # type: ignore[attr-defined]


@pytest.mark.parametrize("breakage", ["absent", "null"])
def test_required_column_absent_or_null_is_refused(breakage: str) -> None:
    row = _pricing_row()
    if breakage == "absent":
        del row["price"]
    else:
        row["price"] = None
    with pytest.raises(SchemaCompatibilityError) as exc:
        from_row(PricingResult, row)
    assert exc.value.contract is PricingResult
    assert exc.value.field == "price"


def test_extra_columns_and_extra_nested_keys_are_ignored_on_read() -> None:
    # the removed-column side of schema evolution: unknown stored data is skipped.
    table = "surface_parameters"
    record = baseline_records()[table]
    contract = REGISTRY[table].contract
    row = to_row(contract, record)
    row["legacy_column"] = "to-be-ignored"
    bundle = json.loads(row["diagnostics"])
    bundle["legacy_diagnostic"] = 42
    row["diagnostics"] = json.dumps(bundle, **_COLUMN_DUMP_ARGS)
    assert from_row(contract, row) == record


def test_nested_optional_bundle_fields_read_back_as_none_when_absent() -> None:
    # SurfaceFitDiagnostics.bound_hits / .converged are the additive-nullable
    # nested fields (rows persisted before they existed).
    table = "surface_parameters"
    contract = REGISTRY[table].contract
    row = to_row(contract, baseline_records()[table])
    bundle = json.loads(row["diagnostics"])
    del bundle["bound_hits"]
    bundle["converged"] = None
    row["diagnostics"] = json.dumps(bundle, **_COLUMN_DUMP_ARGS)
    restored = from_row(contract, row)
    assert restored.diagnostics.bound_hits is None  # type: ignore[attr-defined]
    assert restored.diagnostics.converged is None  # type: ignore[attr-defined]


@pytest.mark.parametrize("breakage", ["absent", "null"])
def test_nested_required_bundle_field_absent_or_null_is_refused(breakage: str) -> None:
    table = "surface_parameters"
    contract = REGISTRY[table].contract
    row = to_row(contract, baseline_records()[table])
    bundle = json.loads(row["diagnostics"])
    if breakage == "absent":
        del bundle["rmse"]
    else:
        bundle["rmse"] = None
    row["diagnostics"] = json.dumps(bundle, **_COLUMN_DUMP_ARGS)
    with pytest.raises(SchemaCompatibilityError) as exc:
        from_row(contract, row)
    assert exc.value.contract is SurfaceFitDiagnostics
    assert exc.value.field == "rmse"


def test_required_field_missing_inside_a_tuple_element_is_refused() -> None:
    table = "baskets"
    contract = REGISTRY[table].contract
    row = to_row(contract, baseline_records()[table])
    legs = json.loads(row["legs"])
    del legs[0]["quantity"]
    row["legs"] = json.dumps(legs, **_COLUMN_DUMP_ARGS)
    with pytest.raises(SchemaCompatibilityError) as exc:
        from_row(contract, row)
    assert exc.value.contract is BasketLeg
    assert exc.value.field == "quantity"


def test_null_required_json_column_is_refused_at_the_top_level() -> None:
    table = "risk_aggregates"
    contract = REGISTRY[table].contract
    row = to_row(contract, baseline_records()[table])
    row["provenance"] = None
    with pytest.raises(SchemaCompatibilityError) as exc:
        from_row(contract, row)
    assert exc.value.contract is contract
    assert exc.value.field == "provenance"


# --- scalar-column repair rules (Parquet reads) -------------------------------


def test_naive_scalar_timestamp_reads_back_utc_aware() -> None:
    table = "market_state_snapshots"
    contract = REGISTRY[table].contract
    record = baseline_records()[table]
    row = to_row(contract, record)
    row["snapshot_ts"] = SNAPSHOT_TS.replace(tzinfo=None)
    restored = from_row(contract, row)
    assert restored.snapshot_ts == SNAPSHOT_TS  # type: ignore[attr-defined]
    assert restored.snapshot_ts.tzinfo is not None  # type: ignore[attr-defined]


def test_midnight_datetime_in_a_date_column_reads_back_as_the_date() -> None:
    table = "market_state_snapshots"
    contract = REGISTRY[table].contract
    record = baseline_records()[table]
    row = to_row(contract, record)
    row["trade_date"] = datetime(2026, 5, 29, 0, 0)
    restored = from_row(contract, row)
    assert restored.trade_date == date(2026, 5, 29)  # type: ignore[attr-defined]
    assert not isinstance(restored.trade_date, datetime)  # type: ignore[attr-defined]


# --- raw-event JSON sidecar (json_io) -----------------------------------------


def test_collector_events_serialize_to_the_committed_golden_text() -> None:
    assert events_to_json(_collector_events()) == _EVENTS_FIXTURE.read_text()


def test_collector_events_round_trip_with_exact_decimals() -> None:
    events = _collector_events()
    restored = events_from_json(events_to_json(events))
    assert restored == events
    assert isinstance(restored[0].field_value, Decimal)
    # exact representation, trailing zero included (Decimal __eq__ would not see it)
    assert str(restored[0].field_value) == "4999.50"
    assert restored[1].field_value is None
    assert restored[1].exchange_ts is None
    assert restored[2].field_value == "open"


def test_collector_event_row_without_provider_defaults_to_deribit() -> None:
    rows = json.loads(events_to_json(_collector_events()))
    for row in rows:
        row.pop("provider", None)
    restored = events_from_json(json.dumps(rows))
    assert {event.provider for event in restored} == {"DERIBIT"}


def test_unexpected_encoded_field_value_is_refused() -> None:
    rows = json.loads(events_to_json(_collector_events()[:1]))
    rows[0]["field_value"] = {"weird": 1}
    with pytest.raises(ValueError):
        events_from_json(json.dumps(rows))


def test_finite_decimal_guard_still_holds_after_decode() -> None:
    # decode path constructs real CollectorEvents, so their own __post_init__
    # rules (finite Decimal, UTC receipt_ts) still apply.
    rows = json.loads(events_to_json(_collector_events()[:1]))
    rows[0]["field_value"] = {"__dec__": "NaN"}
    with pytest.raises(ValueError):
        events_from_json(json.dumps(rows))
    assert math.isnan(float(Decimal("NaN")))  # the guard target really is non-finite


# --- fixture regeneration (a deliberate format change only) -------------------


def test_positivity_policy_fields_are_all_under_the_numeric_gate() -> None:
    """Write-door layering invariant the pydantic swap leans on (review finding).

    The strict positivity/non-negativity TypeAdapters alone would admit np.int64 /
    np.bool_ (pydantic's number protocol); the door stays correct because the
    hand-rolled numeric gate (exactly int/float, bool excluded) runs over every
    numeric field FIRST. That only holds while positive_fields and
    non_negative_fields are subsets of the numeric field set — pin it per spec.
    """
    from algotrading.infra.contracts.registry import numeric_field_names

    for table, spec in REGISTRY.items():
        numeric = set(numeric_field_names(spec.contract))
        policy = set(spec.positive_fields) | set(spec.non_negative_fields)
        assert policy <= numeric, (
            f"{table}: positivity policy names non-numeric fields {sorted(policy - numeric)}"
            " — the strict adapters would see them before the bool/np-type gate"
        )


def _regenerate() -> None:
    payload = {
        "rows": _all_rows(),
        "non_utc_provenance_column": to_row(
            REGISTRY["risk_aggregates"].contract, _non_utc_record()
        )["provenance"],
    }
    _ROWS_FIXTURE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    _EVENTS_FIXTURE.write_text(events_to_json(_collector_events()))
    print(f"wrote {_ROWS_FIXTURE}")
    print(f"wrote {_EVENTS_FIXTURE}")


if __name__ == "__main__":
    import sys

    if "--regenerate" not in sys.argv:
        sys.exit("refusing: pass --regenerate to overwrite the golden fixtures")
    _regenerate()
