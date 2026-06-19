from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

import structlog
from algotrading.core.config import ForwardConfig, PlatformConfig, SurfaceConfig
from algotrading.infra.collectors import is_observation, replay_day
from algotrading.infra.contracts import (
    ForwardCurvePoint,
    InstrumentKey,
    InstrumentMaster,
    IvPoint,
    MarketStateSnapshot,
    Position,
    PricingResult,
    ProjectedOptionAnalytics,
    RawMarketEvent,
    RiskAggregate,
    ScenarioResult,
    SurfaceGrid,
    SurfaceParameters,
    table_for_contract,
)
from algotrading.infra.forwards import (
    ForwardEstimate,
    ForwardPair,
    ParityLine,
    estimate_forward,
    forward_curve_point,
)
from algotrading.infra.iv import IvResult, iv_point, solve_iv
from algotrading.infra.pricing import PRICER_VERSION, from_spot, price, pricing_result
from algotrading.infra.risk import (
    RISK_ENGINE_VERSION,
    PositionRisk,
    Scenario,
    aggregate_lines,
    effective_scenario_version,
    net_lots,
    position_risk,
    risk_aggregate,
    scenario_grid,
    scenario_line_pnls,
    scenario_result,
)
from algotrading.infra.risk.stress_surface import (
    effective_surface_version,
    stress_surface_grid,
)
from algotrading.infra.snapshots import SnapshotBatch, build_snapshots
from algotrading.infra.storage import ParquetStore
from algotrading.infra.surfaces import (
    METHOD_INSUFFICIENT,
    CalendarSlice,
    CalendarViolation,
    SliceFit,
    calendar_violations,
    fit_slice,
    project_surface_fit,
)
from algotrading.infra.surfaces.projection import (
    ProjectionConfig,
    SnapshotMarketState,
    project_grid,
)

from .outputs import ActorOutputs
from .qc_inputs import QcInputs
from .stamping import StampSource, build_stamp
from .valuation_join import default_exercise_style, resolve_valuation_inputs

_LOGGER = structlog.get_logger("actor")


DAY_COUNT = "ACT/365"
_DAYS_PER_YEAR = 365.0

_AGGREGATE_DIMENSION = "underlying"

PROJECTION_AXES_VERSION = "projection-axes-1.2.0"

_MATURITY_MATCH_DECIMALS = 9


@dataclass(frozen=True, slots=True)
class _RiskOutputs:

    pricings: list[PricingResult]
    aggregates: list[RiskAggregate]
    scenarios: list[ScenarioResult]
    netted_lines: tuple[PositionRisk, ...]
    scenario_grid: tuple[Scenario, ...]


@dataclass(frozen=True, slots=True)
class AnalyticsRun:

    outputs: ActorOutputs
    qc_inputs: QcInputs


def _maturity_years(expiry: date, as_of: date) -> float:
    return (expiry - as_of).days / _DAYS_PER_YEAR


def run_analytics(
    events: Sequence[RawMarketEvent],
    positions: Sequence[Position],
    *,
    instruments: Sequence[InstrumentKey],
    masters: Sequence[InstrumentMaster],
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    as_of: datetime,
    calc_ts: datetime,
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style,
    moneyness_buckets: tuple[float, ...] | None = None,
    session_open: bool = True,
    provider: str | None = None,
    projection: ProjectionConfig | None = None,
) -> ActorOutputs:
    return run_analytics_with_qc(
        events,
        positions,
        instruments=instruments,
        masters=masters,
        config=config,
        config_hashes=config_hashes,
        as_of=as_of,
        calc_ts=calc_ts,
        exercise_style_for=exercise_style_for,
        moneyness_buckets=moneyness_buckets,
        session_open=session_open,
        provider=provider,
        projection=projection,
    ).outputs


def run_analytics_with_qc(
    events: Sequence[RawMarketEvent],
    positions: Sequence[Position],
    *,
    instruments: Sequence[InstrumentKey],
    masters: Sequence[InstrumentMaster],
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    as_of: datetime,
    calc_ts: datetime,
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style,
    moneyness_buckets: tuple[float, ...] | None = None,
    session_open: bool = True,
    provider: str | None = None,
    projection: ProjectionConfig | None = None,
) -> AnalyticsRun:
    if moneyness_buckets is None:
        moneyness_buckets = config.surface.moneyness_buckets
    masters_by_key = {master.instrument_key: master.instrument for master in masters}

    observations = tuple(event for event in events if is_observation(event.field_name))
    batch = build_snapshots(
        instruments,
        observations,
        snapshot_ts=as_of,
        qc=config.qc_threshold,
        calc_ts=calc_ts,
        config_hashes=config_hashes,
        session_open=session_open,
    )
    as_of_date = as_of.date()

    forward_estimates, forward_points = _build_forwards(
        batch, masters_by_key, as_of_date,
        as_of=as_of, calc_ts=calc_ts, config_hashes=config_hashes, forward=config.forward,
    )

    iv_points, iv_results = _build_iv_points(
        batch,
        masters_by_key,
        forward_estimates,
        as_of_date,
        config=config,
        as_of=as_of,
        calc_ts=calc_ts,
        config_hashes=config_hashes,
    )

    slice_fits, put_slice_fits, call_slice_fits, surface_params, surface_cells = _build_surfaces(
        iv_points,
        masters_by_key,
        as_of_date,
        as_of=as_of,
        calc_ts=calc_ts,
        config_hashes=config_hashes,
        surface=config.surface,
        moneyness_buckets=moneyness_buckets,
    )

    risk = _build_risk(
        positions,
        batch=batch,
        forwards=forward_estimates,
        slices=slice_fits,
        masters={master.instrument_key: master for master in masters},
        config=config,
        config_hashes=config_hashes,
        as_of=as_of,
        calc_ts=calc_ts,
        exercise_style_for=exercise_style_for,
    )

    projected = _build_projected_analytics(
        slice_fits,
        put_slice_fits=put_slice_fits,
        call_slice_fits=call_slice_fits,
        batch=batch,
        forwards=forward_estimates,
        provider=provider,
        config=config,
        config_hashes=config_hashes,
        as_of=as_of,
        calc_ts=calc_ts,
        projection=projection,
    )

    outputs = ActorOutputs(
        snapshots=batch.snapshots,
        forwards=tuple(forward_points),
        iv_points=tuple(iv_points),
        surface_parameters=tuple(surface_params),
        surface_grid=tuple(surface_cells),
        pricings=tuple(risk.pricings),
        risk_aggregates=tuple(risk.aggregates),
        scenarios=tuple(risk.scenarios),
        projected_analytics=projected,
    )
    qc_inputs = _build_qc_inputs(
        batch,
        masters_by_key,
        as_of_date,
        forward_estimates=forward_estimates,
        iv_results=iv_results,
        slice_fits=slice_fits,
        moneyness_buckets=moneyness_buckets,
        risk=risk,
        portfolio_id=_portfolio_of(positions) if positions else "",
    )
    return AnalyticsRun(outputs=outputs, qc_inputs=qc_inputs)


def _build_qc_inputs(
    batch: SnapshotBatch,
    masters: dict[str, InstrumentKey],
    as_of_date: date,
    *,
    forward_estimates: Sequence[ForwardEstimate],
    iv_results: Sequence[IvResult],
    slice_fits: Sequence[SliceFit],
    moneyness_buckets: tuple[float, ...],
    risk: _RiskOutputs,
    portfolio_id: str,
) -> QcInputs:
    underlying_keys = tuple(
        sorted(key for key in masters if _is_underlying_key(key))
    )
    expected_chain: dict[str, list[str]] = {}
    for key, instrument in masters.items():
        if instrument.is_option():
            expected_chain.setdefault(instrument.underlying_symbol, []).append(key)
    expected_chain_keys = tuple(
        (underlying, tuple(sorted(keys))) for underlying, keys in sorted(expected_chain.items())
    )

    parity_lines = tuple(
        (estimate.underlying, estimate.maturity_years, _parity_line_of(estimate))
        for estimate in forward_estimates
        if estimate.is_usable
    )

    calendar = _calendar_violations_by_underlying(slice_fits, moneyness_buckets)
    iv_by_underlying = _iv_results_by_underlying(iv_results, masters)

    return QcInputs(
        batch=batch,
        underlying_keys=underlying_keys,
        expected_chain_keys=expected_chain_keys,
        forward_estimates=tuple(forward_estimates),
        parity_lines=parity_lines,
        iv_results=iv_by_underlying,
        slice_fits=tuple(slice_fits),
        calendar_violations=calendar,
        risk_lines=risk.netted_lines,
        scenario_grid=risk.scenario_grid,
        portfolio_id=portfolio_id,
    )


def _iv_results_by_underlying(
    iv_results: Sequence[IvResult],
    masters: dict[str, InstrumentKey],
) -> tuple[tuple[str, tuple[IvResult, ...]], ...]:
    grouped: dict[str, list[IvResult]] = {}
    for result in iv_results:
        instrument = masters.get(result.contract_key)
        underlying = instrument.underlying_symbol if instrument is not None else ""
        grouped.setdefault(underlying, []).append(result)
    return tuple((underlying, tuple(grouped[underlying])) for underlying in sorted(grouped))


def _parity_line_of(estimate: ForwardEstimate) -> ParityLine:
    assert estimate.forward is not None and estimate.discount_factor is not None
    slope = -estimate.discount_factor
    intercept = estimate.forward * estimate.discount_factor
    return ParityLine(
        intercept=intercept,
        slope=slope,
        discount_factor=estimate.discount_factor,
        forward=estimate.forward,
        residuals=tuple(point.residual for point in estimate.points),
    )


def _calendar_violations_by_underlying(
    slice_fits: Sequence[SliceFit],
    moneyness_buckets: tuple[float, ...],
) -> tuple[tuple[str, tuple[CalendarViolation, ...]], ...]:
    by_underlying: dict[str, list[SliceFit]] = {}
    for fit in slice_fits:
        if fit.method == METHOD_INSUFFICIENT:
            continue
        by_underlying.setdefault(fit.underlying, []).append(fit)
    out: list[tuple[str, tuple[CalendarViolation, ...]]] = []
    for underlying in sorted(by_underlying):
        fits = by_underlying[underlying]
        curves = [_calendar_slice_of(fit) for fit in fits]
        out.append((underlying, calendar_violations(curves, moneyness_buckets)))
    return tuple(out)


def _calendar_slice_of(fit: SliceFit) -> CalendarSlice:
    observed_ks = [point.log_moneyness for point in fit.raw_points]
    observed_min = min(observed_ks) if observed_ks else None
    observed_max = max(observed_ks) if observed_ks else None
    return CalendarSlice(
        maturity_years=fit.maturity_years,
        total_variance=fit.total_variance,
        observed_k_min=observed_min,
        observed_k_max=observed_max,
    )


def _has_two_sided_option_quote(snapshot: MarketStateSnapshot) -> bool:
    """An option feeds forward/IV only with a genuine two-sided quote: bid AND ask both positive.

    A one-sided / non-positive option quote — the closed-market canary's ``bid==ask<=0``, or a
    last-only fallback — cannot anchor an option mid, so it is excluded from the derived inputs
    *here, in the derived layer*. This is the one rule the live and replay paths share (ADR 0027),
    which is what lets the raw-capture layer faithfully record EVERY observed row (blueprint
    01-architecture §13/§39: a downstream concern must not erase an upstream observation): the
    excluded rows still land in raw and persist as flagged :class:`MarketStateSnapshot`\\ s for the
    quote-health QC — they are simply not fed to the IV solver as if they were a quote. The
    underlying's spot keeps its own last-fallback (resolved separately, not an option mid).
    """
    return snapshot.bid > 0.0 and snapshot.ask > 0.0


def _option_snapshots_by_underlying_maturity(
    batch: SnapshotBatch,
    masters: dict[str, InstrumentKey],
    as_of_date: date,
) -> dict[tuple[str, float], dict[str, list[tuple[InstrumentKey, MarketStateSnapshot]]]]:
    grouped: dict[
        tuple[str, float], dict[str, list[tuple[InstrumentKey, MarketStateSnapshot]]]
    ] = {}
    for snapshot in batch.usable:
        instrument = masters.get(snapshot.instrument_key)
        if instrument is None or not instrument.is_option():
            continue
        if not _has_two_sided_option_quote(snapshot):
            continue
        assert instrument.expiry is not None
        maturity_years = _maturity_years(instrument.expiry, as_of_date)
        if maturity_years <= 0.0:
            continue
        maturity_key = round(maturity_years, _MATURITY_MATCH_DECIMALS)
        bucket = grouped.setdefault((instrument.underlying_symbol, maturity_key), {})
        right = instrument.option_right or ""
        bucket.setdefault(right, []).append((instrument, snapshot))
    return grouped


def _build_forwards(
    batch: SnapshotBatch,
    masters: dict[str, InstrumentKey],
    as_of_date: date,
    *,
    as_of: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
    forward: ForwardConfig,
) -> tuple[list[ForwardEstimate], list[ForwardCurvePoint]]:
    spot_by_underlying = _usable_spot_by_underlying(batch)
    grouped = _option_snapshots_by_underlying_maturity(batch, masters, as_of_date)

    estimates: list[ForwardEstimate] = []
    points: list[ForwardCurvePoint] = []
    for (underlying, maturity_years) in sorted(grouped):
        by_right = grouped[(underlying, maturity_years)]
        pairs = _forward_pairs(by_right)
        if not pairs:
            continue
        estimate = estimate_forward(
            underlying,
            maturity_years,
            pairs,
            config=forward,
            spot=spot_by_underlying.get(underlying),
        )
        estimates.append(estimate)
        if not estimate.is_usable:
            continue
        expiry = _expiry_for(by_right)
        points.append(
            forward_curve_point(
                estimate,
                snapshot_ts=as_of,
                expiry_date=expiry,
                day_count=DAY_COUNT,
                source_snapshot_ts=as_of,
                calc_ts=calc_ts,
                config_hashes=config_hashes,
            )
        )
    return estimates, points


def _forward_pairs(
    by_right: dict[str, list[tuple[InstrumentKey, MarketStateSnapshot]]],
) -> tuple[ForwardPair, ...]:
    calls = {
        instrument.strike: snapshot
        for instrument, snapshot in by_right.get("C", [])
        if instrument.strike is not None
    }
    puts = {
        instrument.strike: snapshot
        for instrument, snapshot in by_right.get("P", [])
        if instrument.strike is not None
    }
    pairs: list[ForwardPair] = []
    for strike in sorted(set(calls) & set(puts)):
        call_snapshot = calls[strike]
        put_snapshot = puts[strike]
        pairs.append(
            ForwardPair(
                strike=strike,
                call_mid=call_snapshot.reference_spot,
                put_mid=put_snapshot.reference_spot,
                liquidity=1.0,
                call_key=call_snapshot.instrument_key,
                put_key=put_snapshot.instrument_key,
            )
        )
    return tuple(pairs)


def _expiry_for(
    by_right: dict[str, list[tuple[InstrumentKey, MarketStateSnapshot]]],
) -> date:
    for entries in by_right.values():
        for instrument, _snapshot in entries:
            if instrument.expiry is not None:
                return instrument.expiry
    raise ValueError("maturity bucket with no expiry")  # pragma: no cover - guarded upstream


def _build_iv_points(
    batch: SnapshotBatch,
    masters: dict[str, InstrumentKey],
    forwards: Sequence[ForwardEstimate],
    as_of_date: date,
    *,
    config: PlatformConfig,
    as_of: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
) -> tuple[list[IvPoint], list[IvResult]]:
    forward_by_key = {
        (estimate.underlying, round(estimate.maturity_years, _MATURITY_MATCH_DECIMALS)): estimate
        for estimate in forwards
        if estimate.is_usable
    }
    rows: list[tuple[tuple[str, float, float, str], IvPoint]] = []
    iv_results: list[IvResult] = []
    for snapshot in batch.usable:
        instrument = masters.get(snapshot.instrument_key)
        if instrument is None or not instrument.is_option():
            continue
        if not _has_two_sided_option_quote(snapshot):
            continue
        assert instrument.expiry is not None and instrument.strike is not None
        maturity_years = _maturity_years(instrument.expiry, as_of_date)
        if maturity_years <= 0.0:
            continue
        estimate = forward_by_key.get(
            (instrument.underlying_symbol, round(maturity_years, _MATURITY_MATCH_DECIMALS))
        )
        if estimate is None or estimate.forward is None or estimate.discount_factor is None:
            continue
        right = instrument.option_right or ""
        result = solve_iv(
            snapshot.reference_spot,
            contract_key=snapshot.instrument_key,
            forward=estimate.forward,
            strike=instrument.strike,
            maturity_years=maturity_years,
            discount_factor=estimate.discount_factor,
            option_right=right,
            config=config.solver,
        )
        iv_results.append(result)
        if not result.converged:
            continue
        point = iv_point(
            result,
            snapshot_ts=as_of,
            source_snapshot_ts=as_of,
            calc_ts=calc_ts,
            config_hashes=config_hashes,
        )
        sort_key = (instrument.underlying_symbol, maturity_years, instrument.strike, right)
        rows.append((sort_key, point))
    rows.sort(key=lambda row: row[0])
    return [point for _key, point in rows], iv_results


def _build_surfaces(
    iv_points: Sequence[IvPoint],
    masters: dict[str, InstrumentKey],
    as_of_date: date,
    *,
    as_of: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
    surface: SurfaceConfig,
    moneyness_buckets: tuple[float, ...],
) -> tuple[
    list[SliceFit], list[SliceFit], list[SliceFit], list[SurfaceParameters], list[SurfaceGrid]
]:
    by_maturity: dict[tuple[str, float, date], list[IvPoint]] = {}
    for point in iv_points:
        instrument = masters.get(point.contract_key)
        if instrument is None or instrument.expiry is None:
            continue
        maturity_years = _maturity_years(instrument.expiry, as_of_date)
        key = (instrument.underlying_symbol, maturity_years, instrument.expiry)
        by_maturity.setdefault(key, []).append(point)

    slice_fits: list[SliceFit] = []
    put_slice_fits: list[SliceFit] = []
    call_slice_fits: list[SliceFit] = []
    params: list[SurfaceParameters] = []
    cells: list[SurfaceGrid] = []
    for (underlying, maturity_years, expiry) in sorted(by_maturity, key=lambda k: (k[0], k[1])):
        points = tuple(by_maturity[(underlying, maturity_years, expiry)])
        fit = fit_slice(
            underlying, maturity_years, points,
            expiry_date=expiry, day_count=DAY_COUNT, config=surface,
        )
        slice_fits.append(fit)

        put_points = tuple(p for p in points if _point_right(masters, p) == "P")
        call_points = tuple(p for p in points if _point_right(masters, p) == "C")
        if put_points:
            put_slice_fits.append(fit_slice(
                underlying, maturity_years, put_points,
                expiry_date=expiry, day_count=DAY_COUNT, config=surface,
            ))
        if call_points:
            call_slice_fits.append(fit_slice(
                underlying, maturity_years, call_points,
                expiry_date=expiry, day_count=DAY_COUNT, config=surface,
            ))

        projection = project_surface_fit(
            fit,
            moneyness_buckets,
            snapshot_ts=as_of,
            source_snapshot_ts=as_of,
            calc_ts=calc_ts,
            config_hashes=config_hashes,
        )
        if projection.parameters is not None:
            params.append(projection.parameters)
        cells.extend(projection.grid_cells)
    return slice_fits, put_slice_fits, call_slice_fits, params, cells


def _point_right(masters: Mapping[str, InstrumentKey], point: IvPoint) -> str | None:
    instrument = masters.get(point.contract_key)
    return None if instrument is None else instrument.option_right


def _build_projected_analytics(
    slice_fits: Sequence[SliceFit],
    *,
    put_slice_fits: Sequence[SliceFit] = (),
    call_slice_fits: Sequence[SliceFit] = (),
    batch: SnapshotBatch,
    forwards: Sequence[ForwardEstimate],
    provider: str | None,
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    as_of: datetime,
    calc_ts: datetime,
    projection: ProjectionConfig | None,
) -> tuple[ProjectedOptionAnalytics, ...]:
    if provider is None or not slice_fits:
        return ()

    grid_qc = config.qc_threshold.grid
    axes = projection or ProjectionConfig.from_band(
        version=PROJECTION_AXES_VERSION,
        band_low_delta=grid_qc.band_low_delta,
        band_high_delta=grid_qc.band_high_delta,
        band_step=grid_qc.band_step,
    )
    spot_by_underlying = _usable_spot_by_underlying(batch)
    discounts_by_underlying: dict[str, dict[float, float]] = {}
    for estimate in forwards:
        if not estimate.is_usable or estimate.discount_factor is None:
            continue
        discounts_by_underlying.setdefault(estimate.underlying, {})[
            round(estimate.maturity_years, _MATURITY_MATCH_DECIMALS)
        ] = estimate.discount_factor

    def _by_underlying(fits: Sequence[SliceFit]) -> dict[str, list[SliceFit]]:
        grouped: dict[str, list[SliceFit]] = {}
        for fit in fits:
            grouped.setdefault(fit.underlying, []).append(fit)
        return grouped

    slices_by_underlying = _by_underlying(slice_fits)
    put_by_underlying = _by_underlying(put_slice_fits)
    call_by_underlying = _by_underlying(call_slice_fits)

    cells: list[ProjectedOptionAnalytics] = []
    for underlying in sorted(slices_by_underlying):
        spot = spot_by_underlying.get(underlying)
        if spot is None:
            continue
        market = SnapshotMarketState(
            underlying=underlying,
            provider=provider,
            spot=spot,
            discount_factors=discounts_by_underlying.get(underlying, {}),
        )
        result = project_grid(
            slices_by_underlying[underlying],
            market,
            put_slices=put_by_underlying.get(underlying, []),
            call_slices=call_by_underlying.get(underlying, []),
            snapshot_ts=as_of,
            source_snapshot_ts=as_of,
            calc_ts=calc_ts,
            projection=axes,
            monetization=config.monetization,
            config_hashes=config_hashes,
        )
        cells.extend(result.cells)
    return tuple(cells)


def _build_risk(
    positions: Sequence[Position],
    *,
    batch: SnapshotBatch,
    forwards: Sequence[ForwardEstimate],
    slices: Sequence[SliceFit],
    masters: dict[str, InstrumentMaster],
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    as_of: datetime,
    calc_ts: datetime,
    exercise_style_for: Callable[[InstrumentKey], str],
) -> _RiskOutputs:
    if not positions:
        return _RiskOutputs([], [], [], (), ())

    valuations = resolve_valuation_inputs(
        positions,
        snapshots=batch,
        forwards=forwards,
        slices=slices,
        masters=masters,
        exercise_style_for=exercise_style_for,
    )

    lines = [
        position_risk(
            portfolio_id=position.portfolio_id,
            quantity=position.quantity,
            valuation=valuations[position.contract_key],
        )
        for position in positions
    ]
    netted = net_lots(lines)

    pricings = [
        _pricing_for_line(line, as_of=as_of, calc_ts=calc_ts, config_hashes=config_hashes)
        for line in netted
    ]

    aggregates = [
        risk_aggregate(
            net,
            valuation_ts=as_of,
            source_snapshot_ts=as_of,
            provenance=build_stamp(
                calc_ts=calc_ts,
                code_version=RISK_ENGINE_VERSION,
                config_hashes=config_hashes,
                sources=_risk_sources(net.lines, as_of),
            ),
        )
        for net in aggregate_lines(
            netted, portfolio_id=_portfolio_of(positions), dimension=_AGGREGATE_DIMENSION
        )
    ]

    def _scenarios_for(grid: tuple[Scenario, ...], version: str) -> list[ScenarioResult]:
        return [
            scenario_result(
                cell,
                valuation_ts=as_of,
                scenario_version=version,
                source_snapshot_ts=as_of,
                provenance=build_stamp(
                    calc_ts=calc_ts,
                    code_version=RISK_ENGINE_VERSION,
                    config_hashes=config_hashes,
                    sources=_risk_sources((cell.line,), as_of),
                ),
            )
            for cell in scenario_line_pnls(netted, grid)
        ]

    families_grid = scenario_grid(config.scenario)
    surface_grid = stress_surface_grid(config.scenario)
    scenarios = _scenarios_for(families_grid, effective_scenario_version(config.scenario))
    scenarios += _scenarios_for(surface_grid, effective_surface_version(config.scenario))
    return _RiskOutputs(
        pricings, aggregates, scenarios, tuple(netted), families_grid + surface_grid
    )


def _pricing_for_line(
    line: PositionRisk, *, as_of: datetime, calc_ts: datetime, config_hashes: Mapping[str, str]
) -> PricingResult:
    valuation = line.valuation
    state = from_spot(
        spot=valuation.spot,
        strike=valuation.strike,
        maturity_years=valuation.maturity_years,
        volatility=valuation.volatility,
        discount_factor=valuation.discount_factor,
        option_right=valuation.option_right,
        carry=valuation.carry,
        exercise_style=valuation.exercise_style,
    )
    greeks = price(state)
    provenance = build_stamp(
        calc_ts=calc_ts,
        code_version=PRICER_VERSION,
        config_hashes=config_hashes,
        sources=(StampSource("market_state_snapshots", (as_of, valuation.contract_key), as_of),),
    )
    return pricing_result(
        state,
        greeks,
        snapshot_ts=as_of,
        contract_key=valuation.contract_key,
        source_snapshot_ts=as_of,
        provenance=provenance,
    )


def _risk_sources(lines: Sequence[PositionRisk], as_of: datetime) -> tuple[StampSource, ...]:
    return tuple(
        StampSource("market_state_snapshots", (as_of, line.valuation.contract_key), as_of)
        for line in lines
    )


def _portfolio_of(positions: Sequence[Position]) -> str:
    return positions[0].portfolio_id


def _usable_spot_by_underlying(batch: SnapshotBatch) -> dict[str, float]:
    spots: dict[str, float] = {}
    for assessed in batch.assessed:
        if not assessed.assessment.is_usable:
            continue
        snapshot = assessed.snapshot
        if _is_underlying_key(snapshot.instrument_key):
            spots.setdefault(snapshot.underlying, snapshot.reference_spot)
    return spots


def _is_underlying_key(instrument_key: str) -> bool:
    fields = instrument_key.split("|")
    return len(fields) == 9 and fields[6] == "" and fields[7] == "" and fields[8] == ""


def persist_outputs(store: ParquetStore, outputs: ActorOutputs) -> None:
    tables = (
        (MarketStateSnapshot, outputs.snapshots),
        (ForwardCurvePoint, outputs.forwards),
        (IvPoint, outputs.iv_points),
        (SurfaceParameters, outputs.surface_parameters),
        (SurfaceGrid, outputs.surface_grid),
        (PricingResult, outputs.pricings),
        (RiskAggregate, outputs.risk_aggregates),
        (ScenarioResult, outputs.scenarios),
        (ProjectedOptionAnalytics, outputs.projected_analytics),
    )
    for contract_type, records in tables:
        if not records:
            continue
        store.write(table_for_contract(contract_type), list(records))


def run_day(
    store: ParquetStore,
    trade_date: date,
    positions: Sequence[Position],
    *,
    instruments: Sequence[InstrumentKey],
    masters: Sequence[InstrumentMaster],
    config: PlatformConfig,
    config_hashes: Mapping[str, str],
    as_of: datetime,
    calc_ts: datetime,
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style,
    moneyness_buckets: tuple[float, ...] | None = None,
    correlation_id: str = "",
    persist: bool = True,
    provider: str | None = None,
    projection: ProjectionConfig | None = None,
) -> ActorOutputs:
    events = replay_day(store, trade_date)
    log = _LOGGER.bind(
        correlation_id=correlation_id,
        trade_date=trade_date.isoformat(),
        event_count=len(events),
        position_count=len(positions),
    )
    log.info("actor.run_day.start")

    outputs = run_analytics(
        events,
        positions,
        instruments=instruments,
        masters=masters,
        config=config,
        config_hashes=config_hashes,
        as_of=as_of,
        calc_ts=calc_ts,
        exercise_style_for=exercise_style_for,
        moneyness_buckets=moneyness_buckets,
        provider=provider,
        projection=projection,
    )

    if persist:
        persist_outputs(store, outputs)
        log.info("actor.run_day.persisted")

    log.info(
        "actor.run_day.done",
        snapshot_count=len(outputs.snapshots),
        forward_count=len(outputs.forwards),
        iv_point_count=len(outputs.iv_points),
        surface_parameter_count=len(outputs.surface_parameters),
        risk_aggregate_count=len(outputs.risk_aggregates),
        scenario_count=len(outputs.scenarios),
        persisted=persist,
    )
    return outputs
