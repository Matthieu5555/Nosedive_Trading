"""The actor: drive C's and D's pure functions over an event stream, stamp, persist.

This is the single glue piece of the whole platform. It holds no math of its own —
it transports market state into C's and D's pure functions and writes their stamped
outputs to A's storage. Because the same driver runs over a live event stream and
over the same events replayed off stored raw partitions, surfaces and risk recompute
*identically* live and in replay. That property is the entire architecture, and it
is why the compute step (:func:`run_analytics`) is a pure function of its inputs and
is kept separate from persistence (:func:`persist_outputs`): the headline replay test
drives :func:`run_analytics` from two event sources and compares the returned
:class:`ActorOutputs` as values.

The pipeline, in order, for one as-of instant:

1. ``build_snapshots`` over the raw events → a :class:`snapshots.SnapshotBatch`
   (full set, QC-usable subset, per-snapshot verdicts). The full snapshots are the
   persisted :class:`contracts.MarketStateSnapshot` rows; the usable subset feeds
   everything downstream; the verdicts feed the QC plane separately.
2. For each underlying/maturity with usable option pairs: ``estimate_forward`` →
   keep the rich :class:`forwards.ForwardEstimate` (it carries the discount factor
   the valuation join needs) and project the usable part to a
   :class:`contracts.ForwardCurvePoint`.
3. For each usable option quote: ``solve_iv`` → ``iv_point``
   (:class:`contracts.IvPoint`).
4. For each maturity: ``fit_slice`` over its IV points → keep the rich
   :class:`surfaces.SliceFit` and project ``surface_parameters`` +
   ``surface_grid_cells``.
5. Resolve one :class:`risk.ContractValuationInput` per held contract via
   :func:`actor.valuation_join.resolve_valuation_inputs` (the math-free join), then
   ``position_risk`` → ``aggregate_lines`` → ``risk_aggregate`` and ``scenario_grid``
   → ``scenario_line_pnls`` → ``scenario_result``.

Every derived output carries a provenance stamp. C's ``build_snapshots``,
``forward_curve_point``, ``iv_point``, ``surface_parameters`` and
``surface_grid_cells`` take the injected ``calc_ts``/``config_hashes`` and build their
own stamps; C's ``pricing_result`` and D's ``risk_aggregate``/``scenario_result``
take a stamp the actor builds via :func:`actor.stamping.build_stamp` with the *same*
injected ``calc_ts``. Nothing in this module reads a clock — ``calc_ts`` and
``as_of`` are injected — which is exactly what makes replay byte-identical and what
E's provenance-verification test checks across every persisted row.
"""

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

# Imported from the submodule, not the risk package __init__ (the stress-surface symbols are
# not re-exported there yet — a post-2A follow-up), so this stays off the concurrently-edited
# __init__.
from algotrading.infra.risk.stress_surface import (
    effective_surface_version,
    stress_surface_grid,
)
from algotrading.infra.snapshots import SnapshotBatch, build_snapshots
from algotrading.infra.storage import ParquetStore
from algotrading.infra.surfaces import (
    METHOD_INSUFFICIENT,
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

# Default moneyness buckets for the regularized surface grid, in log-moneyness.
# At-the-money-centered and symmetric so the persisted grid is comparable across
# underlyings; overridable per run.
DEFAULT_MONEYNESS_BUCKETS: tuple[float, ...] = (-0.2, -0.1, 0.0, 0.1, 0.2)

# The one day-count the actor derives maturity in years under, matching the rest of
# the suite (the C seam and golden pipeline use "ACT/365"). It is threaded into the
# forward/surface projections so the persisted day_count label agrees with the
# maturity the actor solved against. Changing it changes every derived maturity.
DAY_COUNT = "ACT/365"
_DAYS_PER_YEAR = 365.0

# Risk aggregation dimension for the persisted RiskAggregate rows. "underlying" is the
# portfolio-level net per underlying — the coarsest coherent net (ADR 0006 §2).
_AGGREGATE_DIMENSION = "underlying"

# The default projection-axes version stamped onto the grid's `projection` config hash. A label
# only — the axis itself is built by `ProjectionConfig.from_band` from the typed `qc_threshold.grid`
# band (edges + step, ADR 0028), not from this string; it enters config_hashes["projection"] so the
# grid is reproducible. Bump only on a deliberate change to the default axes.
# 1.2.0: ±30Δ pas-2 grid (band_step from config; 15 puts + atm + atmp + 15 calls per spanned tenor).
PROJECTION_AXES_VERSION = "projection-axes-1.2.0"

# Maturity matching tolerance, kept in lockstep with the valuation join so the
# forward/slice indexed by a derived maturity resolves the contract built from the
# same derivation.
_MATURITY_MATCH_DECIMALS = 9


@dataclass(frozen=True, slots=True)
class _RiskOutputs:
    """What :func:`_build_risk` produces: the three persisted lists plus QC intermediates.

    ``pricings``/``aggregates``/``scenarios`` are the persisted contract rows; ``netted_lines``
    and ``scenario_grid`` are the in-memory QC intermediates (the netted risk lines and the
    combined scenario grid the reprice ran over), neither persisted.
    """

    pricings: list[PricingResult]
    aggregates: list[RiskAggregate]
    scenarios: list[ScenarioResult]
    netted_lines: tuple[PositionRisk, ...]
    scenario_grid: tuple[Scenario, ...]


@dataclass(frozen=True, slots=True)
class AnalyticsRun:
    """One actor run as a value: the persisted :class:`ActorOutputs` plus its QC intermediates.

    :func:`run_analytics_with_qc` returns this so the live End-of-Day QC stage can run the full
    named-check set over :attr:`qc_inputs`. :attr:`outputs` is exactly what
    :func:`run_analytics` returns and is the byte-identical replay handle; :attr:`qc_inputs` is
    in-memory only and is never persisted, so carrying it changes nothing on disk.
    """

    outputs: ActorOutputs
    qc_inputs: QcInputs


def _maturity_years(expiry: date, as_of: date) -> float:
    """Year fraction from ``as_of`` to ``expiry`` under :data:`DAY_COUNT` (ACT/365).

    Definitional, not pricing: a calendar-day count over a fixed 365-day year. A
    non-positive result (an expired or same-day option) is returned as-is so the
    downstream forward/IV code applies its own degenerate-maturity handling rather
    than the actor silently dropping the contract.
    """
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
    moneyness_buckets: tuple[float, ...] = DEFAULT_MONEYNESS_BUCKETS,
    session_open: bool = True,
    provider: str | None = None,
    projection: ProjectionConfig | None = None,
) -> ActorOutputs:
    """Compute every derived output for one as-of instant — pure, no I/O, no clock.

    A pure function of its inputs: the same events, positions, config, ``as_of`` and
    ``calc_ts`` always return an equal :class:`ActorOutputs`. ``as_of`` is the market
    snapshot/valuation time; ``calc_ts`` is the computation time recorded in every
    provenance stamp. Both are injected so a replay reproduces the result exactly.
    Reserved ``__``-prefixed meta-events (gaps) are skipped via
    ``collectors.is_observation`` before snapshots are built — a gap is data about
    absence, not an observation. Returns an empty-tuple-filled :class:`ActorOutputs`
    when there is nothing to compute (no events, or no positions for the risk tuples),
    never a partial object.

    When ``provider`` is given, the per-maturity surface fits are additionally regridded onto
    the pinned tenor × delta-band analytics grid (WS 1F :func:`surfaces.project_grid`) and the
    resulting provider-stamped :class:`contracts.ProjectedOptionAnalytics` cells are returned in
    :attr:`ActorOutputs.projected_analytics` (and persisted by :func:`persist_outputs`). It is
    optional because the grid is provider-partitioned: a provider-less replay-equality caller
    leaves the grid empty, while the close-capture EOD path supplies the index's provider so a
    real daily fire produces and persists the grid. ``projection`` defaults to the pinned
    P0.1 axes; the $-Greek conventions come from ``config.monetization``.

    This returns only the persisted :class:`ActorOutputs` so it stays the byte-identical replay
    handle the replay/provenance tests compare as values. A live caller that also needs the QC
    intermediates (the rich forwards, IV solver results incl. non-converged, slice fits, netted
    risk lines, snapshot batch) calls :func:`run_analytics_with_qc`, which runs the *same*
    compute and additionally assembles an in-memory :class:`actor.qc_inputs.QcInputs`.
    """
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
    moneyness_buckets: tuple[float, ...] = DEFAULT_MONEYNESS_BUCKETS,
    session_open: bool = True,
    provider: str | None = None,
    projection: ProjectionConfig | None = None,
) -> AnalyticsRun:
    """Run the actor and return the persisted outputs *plus* the in-memory QC intermediates.

    The compute is exactly :func:`run_analytics`' compute — :attr:`AnalyticsRun.outputs` is the
    same byte-identical :class:`ActorOutputs` — but this also collects the rich intermediates the
    pipeline would otherwise discard into an :class:`actor.qc_inputs.QcInputs`, so the live QC
    stage can run the full named-check set (forward stability, parity residual, IV-solver
    convergence over the *full* solver output incl. non-converged, surface fit, calendar sanity,
    Greek sanity, scenario completeness, underlying-quote health, option-chain coverage).

    The QC bundle is carried in memory only and is never persisted, serialized into any contract
    table, or stamped into a manifest — so producing it changes nothing on disk and the
    replay/provenance goldens are unchanged.
    """
    masters_by_key = {master.instrument_key: master.instrument for master in masters}

    # 1. Snapshots over the observed events (gaps are absence-of-data, not quotes).
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

    # 2. Forwards per (underlying, maturity) from the usable option pairs.
    forward_estimates, forward_points = _build_forwards(
        batch, masters_by_key, as_of_date,
        as_of=as_of, calc_ts=calc_ts, config_hashes=config_hashes, forward=config.forward,
    )

    # 3. IV points per usable, converged option quote; keep the full solver output for QC.
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

    # 4. Surface fits per (underlying, maturity); keep the rich SliceFit for the join. The
    #    combined fit is the join/risk reference; the per-side wings (ADR 0048) feed the grid.
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

    # 5. Risk and scenarios over the resolved valuation inputs.
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

    # 6. Pinned tenor × delta-band grid — only when a provider is supplied to stamp the
    #    provider-partitioned cells (the close-capture EOD path supplies it; replay-equality
    #    callers leave it off and the grid is empty).
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
    """Assemble the in-memory QC bundle from the run's rich intermediates — no persistence.

    Reads the same objects the actor already computed (the forwards, the *full* solver output,
    the slice fits, the netted risk lines, the batch) and the membership it can recover from the
    masters (the bare-underlying snapshot keys, the expected option-chain keys per underlying),
    and precomputes the per-underlying calendar no-arb violations over the run's own
    ``moneyness_buckets`` grid. Every value is genuine — none is fabricated to make a check
    runnable — so a degenerate field (no forwards, no positions) yields an empty entry, not a
    placeholder. Nothing here is persisted.
    """
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
    """Group the full solver output by underlying, in solve order within each group.

    The underlying is read from each result's contract master (the canonical key), never parsed
    from the string, so the grouping is exact. A result whose key has no master (it should not
    occur — every solved quote came from a usable snapshot with a master) is bucketed under the
    empty-string underlying rather than dropped, so the convergence ratio still counts it.
    """
    grouped: dict[str, list[IvResult]] = {}
    for result in iv_results:
        instrument = masters.get(result.contract_key)
        underlying = instrument.underlying_symbol if instrument is not None else ""
        grouped.setdefault(underlying, []).append(result)
    return tuple((underlying, tuple(grouped[underlying])) for underlying in sorted(grouped))


def _parity_line_of(estimate: ForwardEstimate) -> ParityLine:
    """A :class:`forwards.ParityLine` view of a usable estimate for ``check_parity_residual``.

    Carries the estimate's genuine ``forward`` and ``discount_factor`` and the per-strike
    residuals already on its fitted points (in strike-point order). ``slope`` and ``intercept``
    are the exact inverse of the line's own relations (``discount_factor == -slope``,
    ``forward == intercept / discount_factor``), so they are recovered, not invented — and the
    check reads only the residuals regardless. Reached only for a usable estimate, so the
    forward/DF are present.
    """
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
    """Per-underlying calendar no-arb violations over the run's log-moneyness grid.

    Probes :func:`surfaces.calendar_violations` across the same ``moneyness_buckets`` the actor
    regularizes the surface grid on (config, never the data) using each fittable slice's own
    total-variance curve. An ``insufficient`` slice has no curve and is skipped (it persists no
    surface either), so an underlying with fewer than two fittable maturities contributes an
    empty violation tuple — calendar-arb-free by construction, not a crash.
    """
    by_underlying: dict[str, list[SliceFit]] = {}
    for fit in slice_fits:
        if fit.method == METHOD_INSUFFICIENT:
            continue
        by_underlying.setdefault(fit.underlying, []).append(fit)
    out: list[tuple[str, tuple[CalendarViolation, ...]]] = []
    for underlying in sorted(by_underlying):
        fits = by_underlying[underlying]
        curves = [(fit.maturity_years, fit.total_variance) for fit in fits]
        out.append((underlying, calendar_violations(curves, moneyness_buckets)))
    return tuple(out)


def _option_snapshots_by_underlying_maturity(
    batch: SnapshotBatch,
    masters: dict[str, InstrumentKey],
    as_of_date: date,
) -> dict[tuple[str, float], dict[str, list[tuple[InstrumentKey, MarketStateSnapshot]]]]:
    """Group usable option snapshots by (underlying, maturity) then by right.

    Reads each usable snapshot's :class:`InstrumentKey` from the master (the snapshot
    has no strike/right of its own), keeps only options with a positive maturity, and
    buckets them so a forward and a slice can be built per maturity. The inner dict is
    keyed by option right (``"C"``/``"P"``) so the forward pairing is a strike join.
    """
    grouped: dict[
        tuple[str, float], dict[str, list[tuple[InstrumentKey, MarketStateSnapshot]]]
    ] = {}
    for snapshot in batch.usable:
        instrument = masters.get(snapshot.instrument_key)
        if instrument is None or not instrument.is_option():
            continue
        assert instrument.expiry is not None  # narrowed by is_option
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
    """Estimate a forward per (underlying, maturity) and project the usable ones.

    Pairs each usable call snapshot with the usable put at the same strike (using each
    option snapshot's labeled ``reference_spot`` as its mid) and anchors the parity fit
    to the underlying's usable spot, so ``estimate_forward`` can imply the carry the
    valuation join needs. The full rich estimates are returned for the join; only the
    usable ones are projected to a stamped :class:`ForwardCurvePoint`, sorted for a
    deterministic output order.
    """
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
    """Join calls and puts at a shared strike into parity pairs, sorted by strike."""
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
    """The single expiry date shared by every option in a maturity bucket."""
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
    """Solve every usable option quote; project an IvPoint only per converged solve.

    Uses the maturity's usable forward (so ``k`` and the discount factor match what
    fed the forward); an unconverged solve is labeled by the solver and simply not
    emitted as a persisted ``IvPoint`` (``iv_point`` would reject it). The persisted
    points are sorted by (underlying, maturity, strike, right) for a deterministic order.

    Returns the persisted points *and* the full list of solver :class:`iv.IvResult`
    objects — converged and non-converged alike, in solve order — so the QC plane's
    ``check_iv_solver_convergence`` can compute an honest non-convergence ratio. The
    second tuple is carried in-memory on :class:`actor.qc_inputs.QcInputs` only; it never
    changes the persisted points (still exactly the converged subset).
    """
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
    """Fit the combined slice plus the per-side wings per (underlying, maturity).

    The rich combined :class:`SliceFit` is kept for every maturity (the join reads it) and is
    what projects into the persisted ``surface_parameters`` / grid cells via
    :func:`surfaces.project_surface_fit` — unchanged, an ``insufficient`` slice projects nothing.

    Per-side surfaces (ADR 0048): the maturity's IV points are split by the option right of their
    instrument into put-only and call-only sets, and a wing with any points is fit on its own. The
    combined fit is bit-for-bit the legacy fit (same inputs, same call); the put/call fits are
    additive and feed only the analytics grid's ``put``/``call`` rows — they are **not** persisted
    as ``surface_parameters`` yet (no per-side consumer of raw SVI params exists; the front toggle
    is a follow-up). Outputs are sorted by (underlying, maturity) for determinism.
    """
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
    """The option right (``C``/``P``) of an IV point's instrument, or ``None`` if unknown."""
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
    """Regrid each underlying's fitted surface(s) onto the pinned tenor × delta-band grid.

    Returns an empty tuple when no ``provider`` is supplied (the grid is provider-partitioned,
    so a provider-less replay-equality run produces none) — so the change is inert for those
    callers and the byte-identical handle is unchanged. With a provider, groups the rich
    :class:`SliceFit` set by underlying, builds the per-underlying :class:`SnapshotMarketState`
    from the batch's usable spot and the usable forward estimates' discount factors (carry == 0,
    forward == spot per the projection convention), and calls :func:`surfaces.project_grid` once
    per underlying. The combined fits solve the strikes and emit the ``combined`` rows; the
    per-side wings (``put_slice_fits``/``call_slice_fits``, ADR 0048) add the ``put``/``call``
    rows at the same strikes. The cell order is a pure function of the config axes, so the
    persisted grid is deterministic. Cells across underlyings are concatenated in
    sorted-underlying order.
    """
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
            # No usable underlying spot — the grid has nothing to price against; skip rather
            # than guess a spot. The surface fits still persist via surface_parameters/grid.
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
    """Resolve valuation inputs, then run the D risk and scenario pipelines.

    Returns empty tuples when there are no positions (the risk tuples are empty, not a
    partial object). The valuation join is the only place C's contracts meet D's input;
    everything after is D's pure math plus the actor's stamp.

    Besides the three persisted contract lists, the returned :class:`_RiskOutputs` carries
    the netted :class:`risk.PositionRisk` lines and the combined scenario grid the stress
    reprice ran over — both in-memory only, for the QC plane's ``check_greek_sanity`` and
    ``check_scenario_completeness``. Neither is persisted; the persisted scenario rows are
    still exactly the ones the reprice produced.
    """
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

    # The families grid (spot/vol/crash/time roll) and, additively, the WS 2B cartesian
    # (spot × vol) stress surface — both full-reprice, both into scenario_results with distinct
    # ids (families vs surf_), so the stress page reads its surface back read-only. The cron is
    # the sole writer (ADR 0034); the BFF never computes.
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
    """Reprice one netted line into a stamped :class:`PricingResult`.

    Builds the pricing state from the line's resolved valuation (so the persisted price
    and Greeks reproduce the risk line's per-unit numbers) and stamps it against the
    line's own snapshot. The actor builds this stamp because ``pricing_result`` takes a
    pre-built provenance (it has no ``calc_ts`` of its own).
    """
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
    """Name every line's market-state snapshot as a source row for the stamp."""
    return tuple(
        StampSource("market_state_snapshots", (as_of, line.valuation.contract_key), as_of)
        for line in lines
    )


def _portfolio_of(positions: Sequence[Position]) -> str:
    """The single portfolio id the positions belong to."""
    return positions[0].portfolio_id


def _usable_spot_by_underlying(batch: SnapshotBatch) -> dict[str, float]:
    """Map each underlying to the reference spot of its usable underlying snapshot."""
    spots: dict[str, float] = {}
    for assessed in batch.assessed:
        if not assessed.assessment.is_usable:
            continue
        snapshot = assessed.snapshot
        if _is_underlying_key(snapshot.instrument_key):
            spots.setdefault(snapshot.underlying, snapshot.reference_spot)
    return spots


def _is_underlying_key(instrument_key: str) -> bool:
    """True when a canonical key's three option-only trailing slots are blank."""
    fields = instrument_key.split("|")
    return len(fields) == 9 and fields[6] == "" and fields[7] == "" and fields[8] == ""


def persist_outputs(store: ParquetStore, outputs: ActorOutputs) -> None:
    """Write every non-empty output tuple to its contract table, validated by A.

    Routes each contract to its table via ``contracts.table_for_contract`` and writes
    through ``store.write`` (write-ahead validation, all-or-nothing per table). The
    derived tables are replace-semantics, so re-persisting a recomputed as-of replaces
    just those partitions and never touches the append-only raw layer. Idempotent for
    a fixed :class:`ActorOutputs`: persisting the same outputs twice leaves identical
    partition bytes.
    """
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
    moneyness_buckets: tuple[float, ...] = DEFAULT_MONEYNESS_BUCKETS,
    correlation_id: str = "",
    persist: bool = True,
    provider: str | None = None,
    projection: ProjectionConfig | None = None,
) -> ActorOutputs:
    """Replay a stored day's raw events through the actor and persist the outputs.

    The disk entry point: reads the day's raw events in canonical order via
    ``collectors.replay_day`` and feeds them to :func:`run_analytics`, so the analytics
    always derive from the immutable raw layer. The live path differs only in that a
    broker session populated that raw layer first (through B's collector); it then
    calls this same function, which is what makes live and replay one code path rather
    than two that drift. ``correlation_id`` is bound to the structured log line linking
    this analytics run to the collector session that produced its events. Persists when
    ``persist`` is True and returns the :class:`ActorOutputs` either way.
    """
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
