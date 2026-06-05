"""Headline (cross-cutting): every C/D output in storage carries a real stamp.

The other workstreams *claim* determinism and provenance; this test checks rather
than trusts. It drives the actor over a full day so every derived table lands rows,
then walks every persisted C/D record and asserts its provenance stamp is present,
well-formed (its hash recomputes from its contents — ``provenance.validate_stamp``),
and non-empty in the ways that matter: a real ``calc_ts``, a non-empty
``code_version`` and ``config_hash``, and at least one source record with a matching
timestamp. A stamp that is merely *present* but lineage-empty would pass a shape
check and still be a wiring bug, so the lineage is asserted, not just the object.

This is E's invariant to enforce (TESTING.md, "E → all").
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from actor import run_day
from config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from contracts import (
    InstrumentMaster,
    Position,
    RawMarketEvent,
)
from contracts.instrument_key import InstrumentKey
from fixtures.events import quote_events
from fixtures.library import ChainFixture, get_fixture
from provenance import ProvenanceValidationError, validate_stamp
from storage import ParquetStore

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
CONFIG_HASH = "cfg-hash-prov"

# Every derived table a populated day must land rows in. The walk asserts each one
# is actually present, so a silently-unpersisted family fails loudly rather than
# being skipped (an empty table would otherwise pass a "walk what's there" check).
DERIVED_TABLES = (
    "market_state_snapshots",
    "forward_curve",
    "iv_points",
    "surface_parameters",
    "surface_grid",
    "pricing_results",
    "risk_aggregates",
    "scenario_results",
)


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", underlyings=("AAPL",), exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
        scenario=ScenarioConfig(
            version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05, -0.05)
        ),
    )


def _master(instrument: InstrumentKey) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=AS_OF.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _chain_inputs(
    chain: ChainFixture,
) -> tuple[list[RawMarketEvent], list[InstrumentKey], list[InstrumentMaster]]:
    spot = chain.underlying_spot
    events = list(
        quote_events(
            chain.underlying, bid=spot - 0.05, ask=spot + 0.05, last=spot, ts=AS_OF,
            session_id=chain.underlying.canonical(),
        )
    )
    instruments = [chain.underlying]
    masters = [_master(chain.underlying)]
    for quote in chain.quotes:
        events += list(
            quote_events(
                quote.instrument, bid=quote.bid, ask=quote.ask, last=quote.last, ts=AS_OF,
                session_id=quote.instrument.canonical(),
            )
        )
        instruments.append(quote.instrument)
        masters.append(_master(quote.instrument))
    return events, instruments, masters


def _positions(chain: ChainFixture) -> list[Position]:
    calls = [q.instrument for q in chain.quotes if q.instrument.option_right == "C"]
    return [
        Position(valuation_ts=AS_OF, portfolio_id="pf-prov", contract_key=c.canonical(),
                 quantity=q, source="record")
        for c, q in zip(calls[:3], [10.0, -5.0, 3.0], strict=True)
    ]


def _populated_store(tmp_path: Path) -> ParquetStore:
    chain = get_fixture("synthetic_known_answer")
    events, instruments, masters = _chain_inputs(chain)
    store = ParquetStore(tmp_path)
    store.write("raw_market_events", events)
    run_day(
        store, AS_OF.date(), _positions(chain), instruments=instruments, masters=masters,
        config=_config(), config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS, persist=True,
    )
    return store


def test_every_derived_table_lands_at_least_one_row(tmp_path: Path) -> None:
    # The walk below is only meaningful if every derived family is actually persisted;
    # pin that here so a future regression that stops persisting one table is caught.
    store = _populated_store(tmp_path)
    for table in DERIVED_TABLES:
        assert store.read(table), f"{table} persisted no rows for a populated day"


def test_every_persisted_cd_output_carries_a_wellformed_nonempty_stamp(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)

    checked = 0
    for table in DERIVED_TABLES:
        records = store.read(table)
        assert records, f"{table}: nothing persisted"
        for record in records:
            stamp = getattr(record, "provenance", None)
            assert stamp is not None, f"{table}: a record has no provenance stamp"

            # Well-formed: the hash recomputes from the contents. validate_stamp
            # raises on a tampered or malformed stamp; here it must pass.
            validate_stamp(stamp)

            # Non-empty in the ways that matter — a present-but-hollow stamp is the
            # wiring bug this test exists to catch.
            assert stamp.calc_ts == CALC_TS, f"{table}: calc_ts not the injected value"
            assert stamp.code_version, f"{table}: empty code_version"
            assert stamp.config_hash == CONFIG_HASH, f"{table}: config_hash not threaded through"
            assert stamp.source_records, f"{table}: stamp has no source lineage"
            assert len(stamp.source_timestamps) == len(stamp.source_records), (
                f"{table}: source_timestamps and source_records disagree in length"
            )
            checked += 1

    # Guard against a vacuous pass: a real day stamps many rows across eight tables.
    assert checked >= len(DERIVED_TABLES)


_MULTI_CHAINS = ("liquid_aapl", "liquid_msft", "liquid_spy")


def _multi_populated_store(tmp_path: Path) -> ParquetStore:
    """A day driven over three underlyings, persisted — a multi-partition stamp walk.

    The single-underlying walk above proves a stamp lands on one partition's rows; a
    wiring bug that stamps only the first underlying, or that reuses one underlying's
    lineage for another, only shows up when more than one underlying is present.
    """
    events: list[RawMarketEvent] = []
    instruments: list[InstrumentKey] = []
    masters: list[InstrumentMaster] = []
    positions: list[Position] = []
    for name in _MULTI_CHAINS:
        chain = get_fixture(name)
        chain_events, chain_instruments, chain_masters = _chain_inputs(chain)
        events += chain_events
        instruments += chain_instruments
        masters += chain_masters
        positions += _positions(chain)[:1]  # one position per underlying

    store = ParquetStore(tmp_path)
    store.write("raw_market_events", events)
    run_day(
        store, AS_OF.date(), positions, instruments=instruments, masters=masters,
        config=_config(), config_hash=CONFIG_HASH, as_of=AS_OF, calc_ts=CALC_TS, persist=True,
    )
    return store


def test_no_stamp_cites_lineage_recorded_after_the_computation_ran(tmp_path: Path) -> None:
    # Provenance is causal: a derived value's sources are observations that existed when
    # it was computed, so no source timestamp may be later than the stamp's calc_ts. A
    # stamp citing a "future" source would be a determinism bug (lineage wired from the
    # wrong record) that the non-empty checks above would miss. Walk a multi-underlying
    # day so the check spans every partition, not just AAPL's.
    store = _multi_populated_store(tmp_path)
    checked = 0
    for table in DERIVED_TABLES:
        for record in store.read(table):
            stamp = record.provenance
            for ts in stamp.source_timestamps:
                assert ts <= stamp.calc_ts, (
                    f"{table}: a source timestamp {ts} is later than calc_ts {stamp.calc_ts}"
                )
            checked += 1
    assert checked >= len(DERIVED_TABLES)


def test_every_underlying_in_a_multi_underlying_day_carries_a_wellformed_stamp(
    tmp_path: Path,
) -> None:
    # Per-underlying market-data families must stamp each underlying independently; a
    # stamp present only on the first is a real merge-era hazard. Assert all three
    # underlyings land rows in each per-underlying table and every one validates.
    store = _multi_populated_store(tmp_path)
    per_underlying_tables = (
        "market_state_snapshots", "forward_curve", "iv_points",
        "surface_parameters", "surface_grid",
    )
    for table in per_underlying_tables:
        underlyings = {underlying for _date, underlying in store.list_partitions(table)}
        assert underlyings == {"AAPL", "MSFT", "SPY"}, f"{table}: not all underlyings stamped"
        for record in store.read(table):
            validate_stamp(record.provenance)
            assert record.provenance.config_hash == CONFIG_HASH
            assert record.provenance.source_records, f"{table}: empty lineage on a row"


def test_a_tampered_stamp_is_rejected_by_the_same_validator(tmp_path: Path) -> None:
    # The verification leans on validate_stamp; prove it actually rejects a bad stamp
    # rather than passing everything, so the test above is not trivially green.
    import dataclasses

    store = _populated_store(tmp_path)
    good = store.read("iv_points")[0].provenance
    tampered = dataclasses.replace(good, config_hash=good.config_hash + "-tampered")
    with pytest.raises(ProvenanceValidationError):
        validate_stamp(tampered)
