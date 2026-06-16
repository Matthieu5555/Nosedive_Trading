from __future__ import annotations

from pathlib import Path

import pytest
from algotrading.infra.contracts import (
    ContractValidationError,
    DailyBar,
    MarketStateSnapshot,
    PricingResult,
    spec_for_table,
    table_for_contract,
    validate_record,
)
from algotrading.infra.storage import ParquetStore
from fixtures.records import TRADE_DATE, make_record


def _pricing_result(**overrides: object) -> PricingResult:
    full_dollar_layer: dict[str, object] = {"dollar_theta": -0.0000274, "dollar_rho": 0.0003}
    return make_record("pricing_results", **{**full_dollar_layer, **overrides})


def _daily_bar(provider: str = "IBKR", underlying: str = "AAPL", **overrides: object) -> DailyBar:
    ohlc: dict[str, object] = {
        "open": 99.0, "high": 101.5, "low": 98.5, "close": 100.25,
        "volume": 1_234_567.0, "source": "cp-rest",
    }
    return make_record("daily_bar", provider=provider, underlying=underlying,
                       **{**ohlc, **overrides})


def test_pricing_result_with_full_dollar_layer_round_trips(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    record = _pricing_result()
    store.write("pricing_results", [record])
    read_back = store.read("pricing_results")
    assert read_back == [record]
    assert read_back[0].dollar_theta == pytest.approx(-0.0000274)
    assert read_back[0].dollar_rho == pytest.approx(0.0003)
    assert read_back[0].provenance.stamp_hash == record.provenance.stamp_hash


def test_old_pricing_partition_without_the_two_new_fields_still_reads(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    record = _pricing_result(dollar_theta=None, dollar_rho=None)
    store.write("pricing_results", [record])
    read_back = store.read("pricing_results")
    assert read_back == [record]
    assert read_back[0].dollar_theta is None
    assert read_back[0].dollar_rho is None


def test_malformed_pricing_result_is_rejected(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    malformed = _pricing_result(vega=-1.0)
    with pytest.raises(ContractValidationError) as info:
        store.write("pricing_results", [malformed])
    assert info.value.field == "vega"


def test_daily_bar_round_trips_with_provider_partition(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    bar = _daily_bar()
    store.write("daily_bar", [bar])
    read_back = store.read("daily_bar")
    assert read_back == [bar]
    layer = spec_for_table("daily_bar").layer
    expected = (
        tmp_path
        / layer
        / "daily_bar"
        / "provider=IBKR"
        / f"trade_date={TRADE_DATE.isoformat()}"
        / "underlying=AAPL"
        / "data.parquet"
    )
    assert expected.exists()


def test_two_providers_same_symbol_date_land_in_disjoint_partitions(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ibkr = _daily_bar(provider="IBKR", close=100.25)
    saxo = _daily_bar(provider="SAXO", close=100.30)
    store.write("daily_bar", [ibkr, saxo])
    layer = spec_for_table("daily_bar").layer
    base = tmp_path / layer / "daily_bar"
    assert (base / "provider=IBKR").is_dir()
    assert (base / "provider=SAXO").is_dir()
    assert store.read("daily_bar", provider="IBKR") == [ibkr]
    assert store.read("daily_bar", provider="SAXO") == [saxo]
    assert sorted(store.read("daily_bar"), key=lambda b: b.provider) == [ibkr, saxo]


def test_malformed_daily_bar_high_below_low_is_rejected(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    malformed = _daily_bar(high=97.0, low=98.5)
    with pytest.raises(ContractValidationError) as info:
        store.write("daily_bar", [malformed])
    assert info.value.field == "high"


def test_daily_bar_close_outside_range_is_rejected() -> None:
    with pytest.raises(ContractValidationError) as info:
        validate_record("daily_bar", _daily_bar(close=200.0))
    assert info.value.field == "close"


def test_daily_bar_and_snapshot_are_distinct_registry_entries() -> None:
    assert table_for_contract(DailyBar) == "daily_bar"
    assert table_for_contract(MarketStateSnapshot) == "market_state_snapshots"
    assert spec_for_table("daily_bar").contract is DailyBar
    assert spec_for_table("daily_bar").contract is not MarketStateSnapshot
    assert not isinstance(_daily_bar(), MarketStateSnapshot)


def test_as_of_read_takes_the_latest_version_for_that_date_no_look_ahead(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    live = _daily_bar(close=100.25)
    store.write("daily_bar", [live])
    restated = _daily_bar(close=100.99)
    store.write("daily_bar", [restated], version="recompute-2")

    default_read = store.read("daily_bar")
    assert default_read == [live]
    assert default_read[0].close == pytest.approx(100.25)

    versions = store.list_versions("daily_bar", TRADE_DATE, "AAPL", provider="IBKR")
    assert versions == ["recompute-2"]
    restated_read = store.read("daily_bar", version="recompute-2")
    assert restated_read == [restated]
