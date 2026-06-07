"""P0 contract pins: PricingResult dollar layer, the DailyBar product, and as-of reads.

Seam tests in the C->A / B->A pattern (TESTING.md): a contract round-trips through A's store
write/read and validates; a malformed instance is rejected at the write door, not coerced;
and the no-look-ahead read returns the bar captured *for* a date, never a later restatement.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import source_ref, stamp
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

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
TRADE_DATE = date(2026, 5, 29)


def _stamp(source: str) -> object:
    return stamp(
        calc_ts=TS,
        code_version="p0-test",
        config_hashes={"cfg": "cfg-0"},
        source_records=(source_ref("raw_market_events", "sess-p0", source),),
        source_timestamps=(TS,),
    )


def _pricing_result(**overrides: object) -> PricingResult:
    base = dict(
        snapshot_ts=TS,
        contract_key="AAPL|OPT|C|100",
        pricer_version="px-1",
        price=5.0,
        delta=0.5,
        gamma=0.02,
        vega=0.1,
        theta=-0.01,
        rho=0.03,
        dollar_delta=50.0,
        dollar_gamma=2.0,
        dollar_vega=10.0,
        dollar_theta=-0.0000274,
        dollar_rho=0.0003,
        source_snapshot_ts=TS,
        provenance=_stamp("px:AAPL|OPT|C|100"),
    )
    base.update(overrides)
    return PricingResult(**base)  # type: ignore[arg-type]


def _daily_bar(provider: str = "IBKR", underlying: str = "AAPL", **overrides: object) -> DailyBar:
    base = dict(
        provider=provider,
        underlying=underlying,
        trade_date=TRADE_DATE,
        open=99.0,
        high=101.5,
        low=98.5,
        close=100.25,
        volume=1_234_567.0,
        bar_type="1d-TRADES",
        source="cp-rest",
        provenance=_stamp(f"bar:{provider}:{underlying}"),
    )
    base.update(overrides)
    return DailyBar(**base)  # type: ignore[arg-type]


# -- PricingResult: the completed dollar layer ------------------------------------------
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
    # Additive-nullable: a PricingResult with dollar_theta/dollar_rho unset (an older
    # partition's shape) writes and reads back with them None, not a schema failure.
    store = ParquetStore(tmp_path)
    record = _pricing_result(dollar_theta=None, dollar_rho=None)
    store.write("pricing_results", [record])
    read_back = store.read("pricing_results")
    assert read_back == [record]
    assert read_back[0].dollar_theta is None
    assert read_back[0].dollar_rho is None


def test_malformed_pricing_result_is_rejected(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    malformed = _pricing_result(vega=-1.0)  # vega must be non-negative
    with pytest.raises(ContractValidationError) as info:
        store.write("pricing_results", [malformed])
    assert info.value.field == "vega"


# -- DailyBar: the underlying price-history product -------------------------------------
def test_daily_bar_round_trips_with_provider_partition(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    bar = _daily_bar()
    store.write("daily_bar", [bar])
    read_back = store.read("daily_bar")
    assert read_back == [bar]
    # It landed under daily_bar/provider=IBKR/trade_date=.../underlying=AAPL (ADR 0034 §4).
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
    # Two separate provider segments on disk, no mixing.
    layer = spec_for_table("daily_bar").layer
    base = tmp_path / layer / "daily_bar"
    assert (base / "provider=IBKR").is_dir()
    assert (base / "provider=SAXO").is_dir()
    # A provider-scoped read returns only that source.
    assert store.read("daily_bar", provider="IBKR") == [ibkr]
    assert store.read("daily_bar", provider="SAXO") == [saxo]
    # A cross-provider read returns both.
    assert sorted(store.read("daily_bar"), key=lambda b: b.provider) == [ibkr, saxo]


def test_malformed_daily_bar_high_below_low_is_rejected(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    # high < low is corrupt OHLC; it must be rejected, not coerced.
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
    # A DailyBar is not a snapshot and vice versa (distinct frozen types).
    assert not isinstance(_daily_bar(), MarketStateSnapshot)


def test_as_of_read_takes_the_latest_version_for_that_date_no_look_ahead(tmp_path: Path) -> None:
    # No look-ahead: a restated bar (version=<V>) must not leak backward onto an earlier
    # trade_date. A default (version-blind) read returns the LIVE bar for D, never a later
    # restatement; the restatement is only returned when explicitly asked for by version.
    store = ParquetStore(tmp_path)
    live = _daily_bar(close=100.25)
    store.write("daily_bar", [live])
    restated = _daily_bar(close=100.99)
    store.write("daily_bar", [restated], version="recompute-2")

    # Default read: the live bar for D, not the restatement.
    default_read = store.read("daily_bar")
    assert default_read == [live]
    assert default_read[0].close == pytest.approx(100.25)

    # The restatement is reachable only by its explicit version, scoped to its provider.
    versions = store.list_versions("daily_bar", TRADE_DATE, "AAPL", provider="IBKR")
    assert versions == ["recompute-2"]
    restated_read = store.read("daily_bar", version="recompute-2")
    assert restated_read == [restated]
