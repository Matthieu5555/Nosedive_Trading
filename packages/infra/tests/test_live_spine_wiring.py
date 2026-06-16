from __future__ import annotations

import functools
import math
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SignalEntryConfig,
    SolverConfig,
    StrikeSelectionConfig,
    UniverseConfig,
)
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra.actor import IndexBasket
from algotrading.infra.connectivity import ManualClock
from algotrading.infra.contracts import InstrumentMaster, ProjectedOptionAnalytics
from algotrading.infra.orchestration import RunnerDeps, run_fire
from algotrading.infra.orchestration.eod_runner import FiredIndex, default_stages_builder
from algotrading.infra.pricing import from_forward, price_european
from algotrading.infra.storage import ParquetStore, RunRegistry
from algotrading.infra.universe import (
    AvailableChain,
    CalendarResolver,
    ChainSelection,
    DeltaBandMarket,
    IndexRegistry,
    TenorMarket,
    parse_index_registry,
    plan_chain,
    select_strikes,
)
from fixtures.events import quote_events
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG, make_option, make_underlying

TRADE_DATE = date(2026, 3, 12)
SPX_CLOSE = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)
CLOCK_NOW = datetime(2026, 3, 12, 22, 0, tzinfo=UTC)
PROVIDER = "IBKR"


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(
            version="u-1",
            exchange="SMART",
            strike_selection=StrikeSelectionConfig(version="ss-1"),
        ),
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


def _registry() -> IndexRegistry:
    return parse_index_registry(
        {
            "SPX": {
                "name": "S&P 500",
                "calendar": "XNYS",
                "currency": "USD",
                "ibkr": {"conid": 0, "secType": "IND", "exchange": "CBOE"},
                "enabled": True,
            }
        }
    )


_SPOT = 100.0
_EXPIRIES = (date(2026, 4, 11), date(2026, 6, 10), date(2026, 9, 8))
_STRIKES = (70.0, 80.0, 85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0, 130.0)


def _master(instrument, as_of: datetime) -> InstrumentMaster:  # type: ignore[no-untyped-def]
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=as_of.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _grid_basket(symbol: str, as_of: datetime) -> IndexBasket:
    underlying = make_underlying(symbol)
    events = list(
        quote_events(
            underlying, bid=_SPOT - 0.05, ask=_SPOT + 0.05, last=_SPOT, ts=as_of,
            session_id="u",
        )
    )
    instruments = [underlying]
    masters = [_master(underlying, as_of)]
    for index, expiry in enumerate(_EXPIRIES):
        bump = 2.0 * (index + 1)
        for strike in _STRIKES:
            mids = {
                "C": max(_SPOT - strike, 0.0) + 3.0 + bump,
                "P": max(strike - _SPOT, 0.0) + 3.0 + bump,
            }
            for right, mid in mids.items():
                option = make_option(symbol, strike, right, expiry)
                events += list(
                    quote_events(
                        option, bid=mid - 0.10, ask=mid + 0.10, last=mid, ts=as_of,
                        session_id=option.canonical(),
                    )
                )
                instruments.append(option)
                masters.append(_master(option, as_of))
    return IndexBasket(
        instruments=tuple(instruments), events=tuple(events), masters=tuple(masters)
    )


def test_real_eod_fire_flows_capture_to_persisted_grid(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "data")
    config = _config()
    registry = _registry()
    clock = ManualClock(start=CLOCK_NOW)

    def basket_source(
        fired: FiredIndex, trade_date: date, correlation_id: str
    ) -> IndexBasket | None:
        return _grid_basket(fired.entry.symbol, fired.as_of)

    deps = RunnerDeps(
        store=store,
        config=config,
        registry=registry,
        resolver=CalendarResolver(registry, as_of=clock),
        run_repository=RunRegistry(tmp_path / "runs"),
        stages_builder=functools.partial(
            default_stages_builder, basket_source=basket_source
        ),
        clock=clock,
        code_identity="deadbeef",
        environment="test",
    )

    result = run_fire(deps, trade_date=TRADE_DATE, index="SPX")
    assert result is not None
    assert set(result.ran) == {
        "universe_refresh", "collection", "analytics", "reconciliation", "qc",
    }

    grid = store.read("projected_option_analytics")
    assert grid, "the live fire must persist a non-empty ProjectedOptionAnalytics grid"
    assert {row.provider for row in grid} == {PROVIDER}
    assert {row.underlying for row in grid} == {"SPX"}
    assert {row.snapshot_ts for row in grid} == {SPX_CLOSE}
    for row in grid:
        assert math.isfinite(row.strike) and math.isfinite(row.implied_vol)


def _engine_call_delta(*, forward: float, strike: float, maturity_years: float,
                       volatility: float, discount_factor: float) -> float:
    state = from_forward(
        forward=forward, strike=strike, maturity_years=maturity_years,
        volatility=volatility, discount_factor=discount_factor, option_right="C", spot=None,
    )
    return price_european(state).delta / discount_factor


def test_plan_chain_reaches_delta_band_on_production_policy() -> None:
    forward = 100.0
    maturity_years = 0.25
    discount_factor = 1.0
    volatility = 0.20
    bound = 0.30
    min_per_side = 1
    listed = tuple(float(k) for k in range(60, 145, 5))
    expiry = "20260611"

    chain = AvailableChain(
        exchange="SMART", trading_class="SPX", multiplier="100",
        expirations=(expiry,), strikes=listed,
    )
    selection = ChainSelection(max_expiries=1, strike_window_pct=0.35, min_strikes_per_side=2)
    band = DeltaBandMarket(
        selection=StrikeSelectionConfig(
            version="ss-1", delta_bound=bound, min_strikes_per_side=min_per_side
        ),
        markets={
            expiry: TenorMarket(
                forward=forward, maturity_years=maturity_years,
                volatility=volatility, discount_factor=discount_factor,
            )
        },
    )

    plan = plan_chain("SPX", [chain], spot=forward, selection=selection, band=band)
    assert plan is not None

    expected_band = []
    for strike in listed:
        nd1 = _engine_call_delta(
            forward=forward, strike=strike, maturity_years=maturity_years,
            volatility=volatility, discount_factor=discount_factor,
        )
        call_delta, put_delta = nd1, 1.0 - nd1
        if call_delta >= bound and put_delta >= bound:
            expected_band.append(strike)
    expected_band_set = set(expected_band)
    assert expected_band_set, "the oracle band must be non-empty for this surface"
    assert any(k <= forward for k in expected_band_set)
    assert any(k > forward for k in expected_band_set)

    assert set(plan.strikes) == expected_band_set
    pct_window = set(select_strikes(listed, forward, selection))
    assert set(plan.strikes) != pct_window

    fallback = plan_chain("SPX", [chain], spot=forward, selection=selection, band=None)
    assert fallback is not None
    assert set(fallback.strikes) == pct_window


def _grid_row(provider: str) -> ProjectedOptionAnalytics:
    snapshot_ts = SPX_CLOSE
    provenance = stamp(
        calc_ts=snapshot_ts, code_version="projection-1.0.0",
        config_hashes={"projection": "p"},
        source_records=(source_ref("iv_points", snapshot_ts, "SPX|OPT|C|0.25|100"),),
        source_timestamps=(snapshot_ts,),
    )
    return ProjectedOptionAnalytics(
        snapshot_ts=snapshot_ts, provider=provider, underlying="SPX",
        tenor_label="3m", maturity_years=0.25, delta_band="atm", target_delta=0.0,
        log_moneyness=0.0, strike=100.0, forward_price=100.0, implied_vol=0.2,
        total_variance=0.2 * 0.2 * 0.25, price=4.0,
        delta=0.5, gamma=0.04, vega=0.2, theta=-0.01, rho=0.05,
        dollar_delta=50.0, dollar_gamma=4.0, dollar_vega=2.0,
        dollar_delta_unit="$/1.00 move", dollar_gamma_unit="$/1% move",
        dollar_vega_unit="$/1 vol pt",
        model_version="svi-1.0.0", pricer_version="black76-lr-1.0.0",
        source_snapshot_ts=snapshot_ts, provenance=provenance,
    )


def test_two_provider_single_batch_grid_write_keeps_both(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    deribit = _grid_row("DERIBIT")
    ibkr = _grid_row("IBKR")
    assert deribit.snapshot_ts == ibkr.snapshot_ts
    assert (deribit.underlying, deribit.tenor_label, deribit.delta_band) == (
        ibkr.underlying, ibkr.tenor_label, ibkr.delta_band
    )

    store.write("projected_option_analytics", [deribit, ibkr])

    persisted = store.read("projected_option_analytics")
    assert len(persisted) == 2
    assert {row.provider for row in persisted} == {"DERIBIT", "IBKR"}
    assert store.read("projected_option_analytics", provider="DERIBIT")
    assert store.read("projected_option_analytics", provider="IBKR")


def test_calendar_bounds_are_deterministic_for_an_as_of() -> None:
    registry = _registry()
    as_of_a = date(2026, 3, 12)
    as_of_b = date(2026, 1, 5)

    r1 = CalendarResolver(registry, as_of=as_of_a)
    r2 = CalendarResolver(registry, as_of=as_of_a)

    session_day = date(2026, 3, 11)
    assert r1.is_session("SPX", session_day) == r2.is_session("SPX", session_day) is True
    assert r1.session_close("SPX", session_day) == r2.session_close("SPX", session_day)

    between = date(2026, 2, 27)
    earlier = CalendarResolver(registry, as_of=as_of_b)
    assert r1.is_session("SPX", between) is True
    from algotrading.infra.universe.errors import CalendarResolutionError

    with pytest.raises(CalendarResolutionError):
        earlier.is_session("SPX", between)

    clock = ManualClock(start=datetime(2026, 3, 12, 22, 0, tzinfo=UTC))
    r_clock = CalendarResolver(registry, as_of=clock)
    assert r_clock.session_close("SPX", session_day) == r1.session_close("SPX", session_day)


def _grid_deps(store: ParquetStore, tmp_path: Path) -> RunnerDeps:
    clock = ManualClock(start=CLOCK_NOW)

    def basket_source(
        fired: FiredIndex, trade_date: date, correlation_id: str
    ) -> IndexBasket | None:
        return _grid_basket(fired.entry.symbol, fired.as_of)

    return RunnerDeps(
        store=store,
        config=_config(),
        registry=_registry(),
        resolver=CalendarResolver(_registry(), as_of=clock),
        run_repository=RunRegistry(tmp_path / "runs"),
        stages_builder=functools.partial(default_stages_builder, basket_source=basket_source),
        clock=clock,
        code_identity="deadbeef",
        environment="test",
    )


def test_collection_lands_captured_close_events_to_raw(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "data")
    result = run_fire(_grid_deps(store, tmp_path), trade_date=TRADE_DATE, index="SPX")
    assert result is not None
    assert "collection" in result.ran

    expected_ids = {event.event_id for event in _grid_basket("SPX", SPX_CLOSE).events}
    assert expected_ids, "the grid basket must carry events for this to be a meaningful check"

    landed = store.read("raw_market_events", trade_date=TRADE_DATE)
    assert landed, "collection must land the captured close events to the raw layer"
    assert {event.event_id for event in landed} == expected_ids
    assert {event.event_id for event in store.read("raw_market_events")} == expected_ids


def test_collection_raw_landing_is_idempotent_on_prelanded_events(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "data")
    prelanded = list(_grid_basket("SPX", SPX_CLOSE).events)
    store.write("raw_market_events", prelanded)
    before = {event.event_id for event in store.read("raw_market_events")}

    result = run_fire(_grid_deps(store, tmp_path), trade_date=TRADE_DATE, index="SPX")
    assert result is not None
    assert "collection" in result.ran

    after = store.read("raw_market_events")
    assert {event.event_id for event in after} == before
    assert len(after) == len(prelanded)


def test_real_eod_fire_persists_the_strategy_entry_signal_set(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "data")
    base = _config()
    config = base.model_copy(
        update={
            "universe": base.universe.model_copy(
                update={
                    "signals": SignalEntryConfig(
                        version="sig-1",
                        reference_tenor="3m",
                        term_slope_front="1m",
                        term_slope_back="3m",
                    )
                }
            )
        }
    )
    clock = ManualClock(start=CLOCK_NOW)

    def basket_source(
        fired: FiredIndex, trade_date: date, correlation_id: str
    ) -> IndexBasket | None:
        return _grid_basket(fired.entry.symbol, fired.as_of)

    deps = RunnerDeps(
        store=store,
        config=config,
        registry=_registry(),
        resolver=CalendarResolver(_registry(), as_of=clock),
        run_repository=RunRegistry(tmp_path / "runs"),
        stages_builder=functools.partial(default_stages_builder, basket_source=basket_source),
        clock=clock,
        code_identity="deadbeef",
        environment="test",
    )

    result = run_fire(deps, trade_date=TRADE_DATE, index="SPX")
    assert result is not None
    assert "analytics" in result.ran

    signals = store.read(
        "strategy_signals", trade_date=TRADE_DATE, underlying="SPX", provider=PROVIDER
    )
    assert signals, "the live fire must persist a non-empty strategy_signals partition"
    for row in signals:
        assert math.isfinite(row.value)
        assert row.snapshot_ts == SPX_CLOSE
        assert row.source_snapshot_ts == SPX_CLOSE
        assert row.provider == PROVIDER
        assert row.underlying == "SPX"

    entry = config.universe.signals
    grid = store.read(
        "projected_option_analytics", trade_date=TRADE_DATE, underlying="SPX", provider=PROVIDER
    )
    atm = {
        row.tenor_label: row.implied_vol
        for row in grid
        if row.surface_side == "combined" and row.delta_band == "atm"
    }
    assert entry.term_slope_front in atm and entry.term_slope_back in atm, (
        "both slope pillars must be present in the persisted combined-ATM grid for this basket "
        f"(emitted tenors: {sorted(atm)})"
    )
    expected_slope = atm[entry.term_slope_back] - atm[entry.term_slope_front]
    slope_label = f"{entry.term_slope_front}:{entry.term_slope_back}"
    slopes = {
        (row.subject, row.tenor_label): row.value
        for row in signals
        if row.signal_kind == "term_structure_slope"
    }
    assert slopes[("SPX", slope_label)] == pytest.approx(expected_slope)
