from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from algotrading.infra.actor import ActorOutputs, persist_outputs, run_analytics
from algotrading.infra.actor.nautilus_host import (
    RunRequest,
    from_custom_data,
    run_session_via_nautilus,
    to_custom_data,
)
from algotrading.infra.contracts import (
    InstrumentMaster,
    Position,
    RawMarketEvent,
)
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.storage import ParquetStore
from algotrading.infra.storage.partitioning import table_dir
from fixtures.events import quote_events
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG, ChainFixture, get_fixture

AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
CALC_TS = datetime(2026, 5, 29, 16, 0, tzinfo=UTC)
CONFIG_HASH = {"cfg": "cfg-hash-nautilus"}

_MULTI_CHAINS = ("liquid_aapl", "liquid_msft", "liquid_spy")

_DERIVED_TABLES = (
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
        universe=UniverseConfig(version="u-1", exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
        surface=SURFACE_CONFIG,
        forward=FORWARD_CONFIG,
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
            chain.underlying,
            bid=spot - 0.05,
            ask=spot + 0.05,
            last=spot,
            ts=AS_OF,
            session_id=chain.underlying.canonical(),
        )
    )
    instruments = [chain.underlying]
    masters = [_master(chain.underlying)]
    for quote in chain.quotes:
        events += list(
            quote_events(
                quote.instrument,
                bid=quote.bid,
                ask=quote.ask,
                last=quote.last,
                ts=AS_OF,
                session_id=quote.instrument.canonical(),
            )
        )
        instruments.append(quote.instrument)
        masters.append(_master(quote.instrument))
    return events, instruments, masters


def _multi_chain_inputs(
    names: tuple[str, ...],
) -> tuple[list[RawMarketEvent], list[InstrumentKey], list[InstrumentMaster]]:
    events: list[RawMarketEvent] = []
    instruments: list[InstrumentKey] = []
    masters: list[InstrumentMaster] = []
    for name in names:
        chain_events, chain_instruments, chain_masters = _chain_inputs(get_fixture(name))
        events += chain_events
        instruments += chain_instruments
        masters += chain_masters
    return events, instruments, masters


def _positions(chain: ChainFixture) -> list[Position]:
    calls = [q.instrument for q in chain.quotes if q.instrument.option_right == "C"]
    return [
        Position(
            valuation_ts=AS_OF,
            portfolio_id="pf-nautilus",
            contract_key=c.canonical(),
            quantity=q,
            source="record",
        )
        for c, q in zip(calls[:3], [10.0, -5.0, 3.0], strict=False)
    ]


def _request(
    instruments: list[InstrumentKey],
    masters: list[InstrumentMaster],
    positions: list[Position],
    *,
    store: ParquetStore | None = None,
    persist: bool = False,
) -> RunRequest:
    return RunRequest(
        positions=positions,
        instruments=instruments,
        masters=masters,
        config=_config(),
        config_hashes=CONFIG_HASH,
        as_of=AS_OF,
        calc_ts=CALC_TS,
        store=store,
        persist=persist,
    )


def _partition_bytes(root: Path, tables: tuple[str, ...]) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    for table in tables:
        directory = table_dir(root, table)
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.parquet")):
            payloads[f"{table}/{path.relative_to(directory)}"] = path.read_bytes()
    return payloads


def test_bridge_round_trips_every_event_losslessly() -> None:
    events, _instruments, _masters = _multi_chain_inputs(_MULTI_CHAINS)
    assert events
    for event in events:
        assert from_custom_data(to_custom_data(event).data) == event


def test_nautilus_host_matches_direct_run_analytics() -> None:
    events, instruments, masters = _multi_chain_inputs(_MULTI_CHAINS)
    positions = _positions(get_fixture(_MULTI_CHAINS[0]))

    direct = run_analytics(
        events,
        positions,
        instruments=instruments,
        masters=masters,
        config=_config(),
        config_hashes=CONFIG_HASH,
        as_of=AS_OF,
        calc_ts=CALC_TS,
    )
    hosted = run_session_via_nautilus(events, _request(instruments, masters, positions))

    assert not hosted.is_empty()
    assert hosted == direct


def test_persisted_partitions_are_byte_for_byte_identical(tmp_path: Path) -> None:
    events, instruments, masters = _multi_chain_inputs(_MULTI_CHAINS)
    positions = _positions(get_fixture(_MULTI_CHAINS[0]))

    direct_root = tmp_path / "direct"
    hosted_root = tmp_path / "hosted"

    direct_store = ParquetStore(direct_root)
    persist_outputs(
        direct_store,
        run_analytics(
            events,
            positions,
            instruments=instruments,
            masters=masters,
            config=_config(),
            config_hashes=CONFIG_HASH,
            as_of=AS_OF,
            calc_ts=CALC_TS,
        ),
    )

    hosted_store = ParquetStore(hosted_root)
    run_session_via_nautilus(
        events, _request(instruments, masters, positions, store=hosted_store, persist=True)
    )

    direct_bytes = _partition_bytes(direct_root, _DERIVED_TABLES)
    hosted_bytes = _partition_bytes(hosted_root, _DERIVED_TABLES)
    assert direct_bytes
    assert set(hosted_bytes) == set(direct_bytes)
    for relative_path, payload in direct_bytes.items():
        assert hosted_bytes[relative_path] == payload, (
            f"{relative_path}: Nautilus-host bytes differ from direct run"
        )


def test_empty_event_stream_yields_empty_outputs() -> None:
    outputs = run_session_via_nautilus([], _request([], [], []))
    assert outputs == ActorOutputs()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
