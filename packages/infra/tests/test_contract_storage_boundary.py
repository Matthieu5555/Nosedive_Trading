"""Registry-wide contract/storage boundary tests.

These are intentionally seam tests, not unit tests for the registry or the Parquet
adapter in isolation.  A consumer sees a typed contract and a table name; the
storage boundary must preserve that object, place it in the registry-declared
partition, and enforce the registry's derived-record requirements before bytes land.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from algotrading.infra.contracts import ContractValidationError, validate_record
from algotrading.infra.contracts.registry import REGISTRY
from algotrading.infra.storage import ParquetStore
from algotrading.infra.storage.partitioning import (
    provider_of,
    trade_date_of,
    underlying_of,
)
from fixtures.records import baseline_records


def _registered_tables() -> list[str]:
    return sorted(REGISTRY)


def _tables_requiring_source_snapshot() -> list[str]:
    return sorted(
        table for table, spec in REGISTRY.items() if spec.requires_source_snapshot_ts
    )


def _tables_requiring_provenance() -> list[str]:
    return sorted(table for table, spec in REGISTRY.items() if spec.requires_provenance)


@pytest.mark.parametrize("table", _registered_tables())
def test_registered_contract_round_trips_through_real_storage(
    table: str, tmp_path: Path
) -> None:
    """Every table-family contract survives the concrete storage seam unchanged."""
    store = ParquetStore(tmp_path)
    record = baseline_records()[table]

    store.write(table, [record])

    assert store.read(table) == [record]


@pytest.mark.parametrize("table", _registered_tables())
def test_registered_contract_read_by_its_registry_partition(
    table: str, tmp_path: Path
) -> None:
    """The registry's table identity and the partitioning adapter agree for every table."""
    store = ParquetStore(tmp_path)
    record = baseline_records()[table]
    spec = REGISTRY[table]
    provider = provider_of(record) if spec.provider_partitioned else None
    trade_date = trade_date_of(record)
    underlying = underlying_of(record)

    store.write(table, [record])

    assert store.read(
        table,
        trade_date=trade_date,
        underlying=underlying,
        provider=provider,
    ) == [record]


@pytest.mark.parametrize("table", _registered_tables())
def test_registered_contract_partition_is_visible_after_write(
    table: str, tmp_path: Path
) -> None:
    """A write through the contract seam creates the partition callers later discover."""
    store = ParquetStore(tmp_path)
    record = baseline_records()[table]
    expected_partition = (trade_date_of(record), underlying_of(record))

    store.write(table, [record])

    assert expected_partition in store.list_partitions(table)


@pytest.mark.parametrize("table", _tables_requiring_source_snapshot())
def test_derived_contracts_must_reference_their_source_snapshot(table: str) -> None:
    """Derived records crossing the seam must carry the snapshot they were computed from."""
    record = dataclasses.replace(baseline_records()[table], source_snapshot_ts=None)

    with pytest.raises(ContractValidationError) as info:
        validate_record(table, record)

    assert info.value.field == "source_snapshot_ts"


@pytest.mark.parametrize("table", _tables_requiring_provenance())
def test_provenance_required_contracts_must_carry_a_valid_stamp(table: str) -> None:
    """Any persisted derived/evidence record must keep its reproducibility handle."""
    record = dataclasses.replace(baseline_records()[table], provenance=None)

    with pytest.raises(ContractValidationError) as info:
        validate_record(table, record)

    assert info.value.field == "provenance"


def test_mixed_batch_validation_is_all_or_nothing_across_partitions(tmp_path: Path) -> None:
    """A bad record in a later partition must not partially commit earlier records."""
    store = ParquetStore(tmp_path)
    good = baseline_records()["daily_bar"]
    other_day = dataclasses.replace(
        good,
        trade_date=date(2026, 5, 30),
        open=191.0,
        high=192.0,
        low=190.0,
        close=191.5,
    )
    malformed = dataclasses.replace(other_day, high=189.0)

    with pytest.raises(ContractValidationError) as info:
        store.write("daily_bar", [good, malformed])

    assert info.value.field == "high"
    assert store.read("daily_bar") == []


def test_provider_partitioned_batch_keeps_sources_separate(tmp_path: Path) -> None:
    """Two providers for the same key-shaped market slice do not overwrite each other."""
    store = ParquetStore(tmp_path)
    ibkr = baseline_records()["projected_option_analytics"]
    cboe = dataclasses.replace(
        ibkr,
        provider="CBOE",
        price=ibkr.price + 0.25,
        implied_vol=ibkr.implied_vol + 0.01,
    )

    store.write("projected_option_analytics", [ibkr, cboe])

    assert store.read("projected_option_analytics", provider="IBKR") == [ibkr]
    assert store.read("projected_option_analytics", provider="CBOE") == [cboe]
    assert {
        row.provider for row in store.read("projected_option_analytics")
    } == {"IBKR", "CBOE"}


def test_live_recompute_replaces_only_the_target_partition(tmp_path: Path) -> None:
    """Derived live rewrites are partition-scoped, so one recompute cannot erase another."""
    store = ParquetStore(tmp_path)
    aapl = baseline_records()["forward_curve"]
    msft = dataclasses.replace(
        aapl,
        underlying="MSFT",
        forward_price=410.0,
    )
    revised_aapl = dataclasses.replace(aapl, forward_price=aapl.forward_price + 1.0)

    store.write("forward_curve", [aapl, msft])
    store.write("forward_curve", [revised_aapl])

    read_back = store.read("forward_curve")
    by_underlying = {row.underlying: row for row in read_back}
    assert by_underlying == {"AAPL": revised_aapl, "MSFT": msft}


@pytest.mark.parametrize(
    ("table", "mutations"),
    [
        ("market_state_snapshots", {"bid": "190.4"}),
        ("forward_curve", {"forward_price": "191.0"}),
        ("iv_points", {"implied_vol": "0.2"}),
        ("surface_parameters", {"svi_b": "0.10"}),
        ("surface_grid", {"total_variance": "0.01"}),
        ("pricing_results", {"delta": "0.5"}),
        ("projected_option_analytics", {"price": "1.25"}),
        ("positions", {"quantity": "10"}),
        ("risk_aggregates", {"net_delta": "5"}),
        ("scenario_results", {"scenario_pnl": "-25"}),
        ("scenario_attributions", {"delta_pnl": "-20"}),
        ("book_greeks", {"dollar_delta": "950"}),
        ("qc_results", {"measured_value": "0.001"}),
        ("daily_bar", {"close": "190.5"}),
    ],
)
def test_numeric_contract_fields_reject_string_values(
    table: str, mutations: dict[str, Any]
) -> None:
    """Stringified broker/API numbers cannot cross the typed contract boundary."""
    record = dataclasses.replace(baseline_records()[table], **mutations)
    expected_field = next(iter(mutations))

    with pytest.raises(ContractValidationError) as info:
        validate_record(table, record)

    assert info.value.field == expected_field
