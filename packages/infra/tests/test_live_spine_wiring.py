"""Adversarial seam tests for the live EOD orchestration spine (M1–M4, M6/1J).

A code audit found the spine was a "tested skeleton, dead spine": every subsystem passed
with injected fakes, but a real end-of-day timer fire was inert end to end. These tests pin
the *exact* seams the green suite avoided. Each one is written to FAIL on the pre-fix code and
PASS on the wired code:

1. ``test_real_eod_fire_flows_capture_to_persisted_grid`` — drive a fire through the
   PRODUCTION deps path (``build_default_deps`` shape + ``default_stages_builder``, not an
   injected fixture builder) and assert a non-empty ``ProjectedOptionAnalytics`` grid is
   persisted to a TEMP store. Pre-fix ``default_stages_builder`` raised unconditionally, so no
   fire ever ran; nothing called ``project_grid`` or persisted a grid.
2. ``test_plan_chain_reaches_delta_band_on_production_policy`` — ``plan_chain`` selects strikes
   via the delta-band policy when a ``DeltaBandMarket`` is supplied. Pre-fix ``plan_chain``
   only ever called ``select_strikes`` (%-of-spot); ``select_strikes_delta_band`` had zero
   production callers.
3. ``test_two_provider_single_batch_grid_write_keeps_both`` — a single-batch write of two
   providers' grid rows for the same (snapshot, underlying, tenor, band) now succeeds with both
   rows distinct. Pre-fix the PK omitted ``provider`` so the batch-global duplicate-key guard
   false-rejected the second provider's row (M4).
4. ``test_calendar_bounds_are_deterministic_for_an_as_of`` — the resolver's coverage window is
   a pure function of the injected as-of, not of wall-clock today. Pre-fix the resolver built
   unbounded ``exchange_calendars`` whose ``last_session`` is *today + ~1y* and so drifted
   day to day.

Expected values are derived independently of the code under test: the grid's provider is the
known close-capture provider label; the delta-band membership is computed against the pricing
engine directly (the band's own oracle); the two-provider distinctness is a set equality on
hand-stated providers; the calendar determinism is an equality between two resolvers built at
the same as-of, plus an inequality against a different as-of. No test writes under ``data/``.
"""

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

# A close instant for the fire's single index (NYSE 16:00 EDT on the trade date).
TRADE_DATE = date(2026, 3, 12)
SPX_CLOSE = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)
CLOCK_NOW = datetime(2026, 3, 12, 22, 0, tzinfo=UTC)
PROVIDER = "IBKR"  # the close-capture provider label the live grid is stamped with


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
    # XNYS is a real exchange_calendars code so the parser accepts the block; a placeholder
    # conid (the calendar/projection path does not consume it).
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


# --------------------------------------------------------------------------- #
# A multi-maturity, broad-strike close basket that fits a real surface and so  #
# yields a NON-EMPTY pinned-tenor × delta-band grid (intrinsic + premium mids  #
# with a term-structure bump, the same shape as the liquid fixture chain).     #
# --------------------------------------------------------------------------- #
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
    """A close basket that fits a real multi-maturity surface (so the grid is non-empty)."""
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
        bump = 2.0 * (index + 1)  # a term-structure premium so the maturities differ
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


# =========================================================================== #
# 1. A real production-deps fire flows capture → project_grid → persist         #
# =========================================================================== #
def test_real_eod_fire_flows_capture_to_persisted_grid(tmp_path: Path) -> None:
    """The PRODUCTION wiring (default_stages_builder), not a fixture builder, persists a grid.

    Pre-fix: ``default_stages_builder`` raised ``EodRunError`` unconditionally, so a real fire
    exited before any stage; ``project_grid`` had no orchestration caller and no grid was ever
    persisted. This drives a fire through the production stages builder (with only the 1C basket
    source — the genuinely-unclosed seam — injected) and asserts a non-empty
    ``ProjectedOptionAnalytics`` grid lands in a TEMP store.
    """
    store = ParquetStore(tmp_path / "data")
    config = _config()
    registry = _registry()
    clock = ManualClock(start=CLOCK_NOW)

    # Inject ONLY the 1C basket source (the still-open seam); everything else is the real
    # production wiring — the same default_stages_builder build_default_deps uses.
    def basket_source(fired: FiredIndex, trade_date: date) -> IndexBasket | None:
        return _grid_basket(fired.entry.symbol, fired.as_of)

    deps = RunnerDeps(
        store=store,
        config=config,
        registry=registry,
        # The real resolver, bounded to the fire's as-of (the production discipline).
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
    # Every stage ran cleanly (the spine is live end to end, not a blanket raise).
    assert set(result.ran) == {
        "universe_refresh", "collection", "analytics", "reconciliation", "qc",
    }

    # The grid was persisted to the TEMP store, non-empty, under the close-capture provider.
    grid = store.read("projected_option_analytics")
    assert grid, "the live fire must persist a non-empty ProjectedOptionAnalytics grid"
    assert {row.provider for row in grid} == {PROVIDER}
    assert {row.underlying for row in grid} == {"SPX"}
    # Snapshot ts is the index's own session close (the as-of the fire injected).
    assert {row.snapshot_ts for row in grid} == {SPX_CLOSE}
    # Every persisted cell is finite (a labeled gap is never a NaN-bearing row).
    for row in grid:
        assert math.isfinite(row.strike) and math.isfinite(row.implied_vol)


# =========================================================================== #
# 2. plan_chain reaches the delta-band selection on the production policy path   #
# =========================================================================== #
def _engine_call_delta(*, forward: float, strike: float, maturity_years: float,
                       volatility: float, discount_factor: float) -> float:
    """Undiscounted forward call delta N(d1) straight from the pricing engine — the band oracle.

    Independent of chain_planning: builds the call state at carry == 0 and divides the engine's
    discounted spot delta by the discount factor. This is the same number the 30Δ band keys off,
    computed here so the expected membership is derived outside the code under test.
    """
    state = from_forward(
        forward=forward, strike=strike, maturity_years=maturity_years,
        volatility=volatility, discount_factor=discount_factor, option_right="C", spot=None,
    )
    return price_european(state).delta / discount_factor


def test_plan_chain_reaches_delta_band_on_production_policy() -> None:
    """``plan_chain`` selects the 30Δ band when a ``DeltaBandMarket`` is supplied, not %-of-spot.

    Pre-fix ``plan_chain`` always called ``select_strikes`` (%-of-spot); the delta-band code had
    zero production callers. Here a band market is supplied and the planned strikes must equal
    the delta-band block computed independently against the engine — and differ from the
    %-of-spot window (proving the policy actually switched).
    """
    forward = 100.0
    maturity_years = 0.25
    discount_factor = 1.0
    volatility = 0.20
    bound = 0.30
    min_per_side = 1  # keep the floor from padding so the assertion tests the pure band
    # A broad listed ladder so the 30Δ band is a strict interior subset (not the whole ladder).
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

    # Independent oracle: a strike is in the 30Δ band iff BOTH its call-delta magnitude and its
    # put-delta magnitude are >= the bound (the contiguous central block), computed from the
    # engine directly — never from chain_planning. With min_strikes_per_side == 1 and at least
    # one band strike on each side of the forward, the per-side floor does not pad, so the band
    # block IS the kept set.
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
    # Sanity: with min_per_side == 1 the band already has at least one strike on each side of
    # the forward (the policy splits on ``strike <= forward``), so the floor cannot pad.
    assert any(k <= forward for k in expected_band_set)
    assert any(k > forward for k in expected_band_set)

    # plan_chain reached the delta-band code: its strikes equal the engine-derived band block.
    assert set(plan.strikes) == expected_band_set
    # And the band is genuinely different from the %-of-spot window (the policy switched).
    pct_window = set(select_strikes(listed, forward, selection))
    assert set(plan.strikes) != pct_window

    # Control: with no band market, plan_chain falls back to the %-of-spot window verbatim.
    fallback = plan_chain("SPX", [chain], spot=forward, selection=selection, band=None)
    assert fallback is not None
    assert set(fallback.strikes) == pct_window


# =========================================================================== #
# 3. Two-provider single-batch write to projected_option_analytics succeeds      #
# =========================================================================== #
def _grid_row(provider: str) -> ProjectedOptionAnalytics:
    """One ProjectedOptionAnalytics cell for a provider, identical on every non-provider field.

    The (snapshot_ts, underlying, tenor_label, delta_band) tuple is shared between the two
    providers' rows — exactly the case that, pre-M4 (PK without provider), tripped the
    batch-global duplicate-key guard.
    """
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
    """A single batch carrying two providers' grid rows for one cell key lands BOTH (M4 PK seam).

    Pre-fix the PK was (snapshot_ts, underlying, tenor_label, delta_band) — provider absent — so
    two providers' rows produced the SAME key and the batch-global duplicate-key guard raised
    ``DuplicateKeyInBatch`` even though they partition to disjoint provider segments. With
    ``provider`` in the PK the write succeeds and both rows are retained, distinct on provider.
    """
    store = ParquetStore(tmp_path)
    deribit = _grid_row("DERIBIT")
    ibkr = _grid_row("IBKR")
    # Identical on every field except provider — the exact collision the seam false-rejected.
    assert deribit.snapshot_ts == ibkr.snapshot_ts
    assert (deribit.underlying, deribit.tenor_label, deribit.delta_band) == (
        ibkr.underlying, ibkr.tenor_label, ibkr.delta_band
    )

    # The whole point: a SINGLE batch with both providers' rows must not false-reject.
    store.write("projected_option_analytics", [deribit, ibkr])

    persisted = store.read("projected_option_analytics")
    assert len(persisted) == 2
    assert {row.provider for row in persisted} == {"DERIBIT", "IBKR"}
    # Both providers' partitions exist on disk, disjoint.
    assert store.read("projected_option_analytics", provider="DERIBIT")
    assert store.read("projected_option_analytics", provider="IBKR")


# =========================================================================== #
# 4. Calendar/registry determinism: same as-of → same bounds, no wall clock     #
# =========================================================================== #
def test_calendar_bounds_are_deterministic_for_an_as_of() -> None:
    """Two resolvers at the same as-of agree on session bounds; a different as-of moves them.

    Pre-fix the resolver built an unbounded exchange_calendars calendar whose ``last_session``
    is wall-clock *today + ~1 year*, so the coverage window — and which future dates
    ``is_session`` accepts vs rejects as out-of-window — silently drifted day to day. Bounding
    the calendar to the injected as-of makes the window a pure function of the as-of, identical
    across resolvers built at the same as-of and different across as-ofs.
    """
    registry = _registry()
    as_of_a = date(2026, 3, 12)
    as_of_b = date(2026, 1, 5)

    r1 = CalendarResolver(registry, as_of=as_of_a)
    r2 = CalendarResolver(registry, as_of=as_of_a)

    # An in-window known NYSE trading day resolves identically and to the same close instant.
    session_day = date(2026, 3, 11)
    assert r1.is_session("SPX", session_day) == r2.is_session("SPX", session_day) is True
    assert r1.session_close("SPX", session_day) == r2.session_close("SPX", session_day)

    # A date PAST the as-of is outside the bounded window for as_of_a but a session under the
    # later-bounded as_of_b would still reject it (it's past b too) — so we instead show the
    # window edge moves with the as-of: a date that is in-window for the later as-of but beyond
    # the earlier as-of's window raises for the earlier and resolves for the later.
    between = date(2026, 2, 27)  # after as_of_b, before as_of_a
    earlier = CalendarResolver(registry, as_of=as_of_b)
    # For as_of_a the date is inside the window (a real NYSE session → resolves).
    assert r1.is_session("SPX", between) is True
    # For as_of_b the date is BEYOND the window (as_of_b < between) → labeled out-of-window.
    from algotrading.infra.universe.errors import CalendarResolutionError

    with pytest.raises(CalendarResolutionError):
        earlier.is_session("SPX", between)

    # The determinism is independent of any wall clock: the bound is the injected as-of, not
    # date.today(). A clock-shaped as-of (the EOD runner's Clock) drives the same window.
    clock = ManualClock(start=datetime(2026, 3, 12, 22, 0, tzinfo=UTC))
    r_clock = CalendarResolver(registry, as_of=clock)
    assert r_clock.session_close("SPX", session_day) == r1.session_close("SPX", session_day)


# =========================================================================== #
# 5. The collection stage lands the captured close events to the raw layer      #
#    BEFORE analytics (blueprint Part III Step 3/4), idempotently.              #
# =========================================================================== #
def _grid_deps(store: ParquetStore, tmp_path: Path) -> RunnerDeps:
    """Production deps with the grid basket source injected — the only open seam (1C)."""
    clock = ManualClock(start=CLOCK_NOW)

    def basket_source(fired: FiredIndex, trade_date: date) -> IndexBasket | None:
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
    """The live collection stage persists the captured basket's events to ``raw_market_events``.

    Pre-fix the live ``_collection`` summarized an EMPTY session and never wrote raw, so the close
    marks lived only in memory: an analytics failure lost them irrecoverably and the day could not
    be replayed from disk (blueprint Part III Step 3/4 — raw is the evidentiary, replayable
    record). This drives the production wiring and asserts the raw layer holds exactly the captured
    close events, derived independently from the same deterministic basket the source produced.
    """
    store = ParquetStore(tmp_path / "data")
    result = run_fire(_grid_deps(store, tmp_path), trade_date=TRADE_DATE, index="SPX")
    assert result is not None
    assert "collection" in result.ran

    # Independent oracle: the event ids of the very basket the source builds for this fire.
    expected_ids = {event.event_id for event in _grid_basket("SPX", SPX_CLOSE).events}
    assert expected_ids, "the grid basket must carry events for this to be a meaningful check"

    landed = store.read("raw_market_events", trade_date=TRADE_DATE)
    assert landed, "collection must land the captured close events to the raw layer"
    assert {event.event_id for event in landed} == expected_ids
    # Replayable from disk without the broker: the unscoped read returns the same landed set.
    assert {event.event_id for event in store.read("raw_market_events")} == expected_ids


def test_collection_raw_landing_is_idempotent_on_prelanded_events(tmp_path: Path) -> None:
    """A re-fire over already-landed close events is a clean no-op, never an append-only collision.

    ``raw_market_events`` is append-only and content-addressed: re-writing an event whose id is on
    disk would raise ``AppendOnlyViolation`` (the immutable-first-close rule). The collection stage
    therefore filters to the ids not yet present before writing. Here the exact captured events are
    pre-landed, so the fire's collection stage must write nothing new and must not raise — and the
    raw layer is unchanged (no duplicate rows).
    """
    store = ParquetStore(tmp_path / "data")
    prelanded = list(_grid_basket("SPX", SPX_CLOSE).events)
    store.write("raw_market_events", prelanded)
    before = {event.event_id for event in store.read("raw_market_events")}

    # Must complete (no AppendOnlyViolation) and leave the raw layer byte-identical in id-set.
    result = run_fire(_grid_deps(store, tmp_path), trade_date=TRADE_DATE, index="SPX")
    assert result is not None
    assert "collection" in result.ran

    after = store.read("raw_market_events")
    assert {event.event_id for event in after} == before
    assert len(after) == len(prelanded)  # no duplicate rows appended
