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
``surface_grid_cells`` take the injected ``calc_ts``/``config_hash`` and build their
own stamps; C's ``pricing_result`` and D's ``risk_aggregate``/``scenario_result``
take a stamp the actor builds via :func:`actor.stamping.build_stamp` with the *same*
injected ``calc_ts``. Nothing in this module reads a clock — ``calc_ts`` and
``as_of`` are injected — which is exactly what makes replay byte-identical and what
E's provenance-verification test checks across every persisted row.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime

import structlog
from algotrading.core.config import PlatformConfig, SurfaceConfig
from algotrading.infra.collectors import is_observation, replay_day
from algotrading.infra.contracts import (
    ForwardCurvePoint,
    InstrumentKey,
    InstrumentMaster,
    IvPoint,
    MarketStateSnapshot,
    Position,
    PricingResult,
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
    estimate_forward,
    forward_curve_point,
)
from algotrading.infra.iv import iv_point, solve_iv
from algotrading.infra.pricing import PRICER_VERSION, from_spot, price, pricing_result
from algotrading.infra.risk import (
    RISK_ENGINE_VERSION,
    PositionRisk,
    aggregate_lines,
    effective_scenario_version,
    net_lots,
    position_risk,
    risk_aggregate,
    scenario_grid,
    scenario_line_pnls,
    scenario_result,
)
from algotrading.infra.snapshots import SnapshotBatch, build_snapshots
from algotrading.infra.storage import ParquetStore
from algotrading.infra.surfaces import SliceFit, fit_slice, project_surface_fit

from .outputs import ActorOutputs
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

# Maturity matching tolerance, kept in lockstep with the valuation join so the
# forward/slice indexed by a derived maturity resolves the contract built from the
# same derivation.
_MATURITY_MATCH_DECIMALS = 9


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
    config_hash: str,
    as_of: datetime,
    calc_ts: datetime,
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style,
    moneyness_buckets: tuple[float, ...] = DEFAULT_MONEYNESS_BUCKETS,
    session_open: bool = True,
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
        config_hash=config_hash,
        session_open=session_open,
    )
    as_of_date = as_of.date()

    # 2. Forwards per (underlying, maturity) from the usable option pairs.
    forward_estimates, forward_points = _build_forwards(
        batch, masters_by_key, as_of_date, as_of=as_of, calc_ts=calc_ts, config_hash=config_hash
    )

    # 3. IV points per usable, converged option quote.
    iv_points = _build_iv_points(
        batch,
        masters_by_key,
        forward_estimates,
        as_of_date,
        config=config,
        as_of=as_of,
        calc_ts=calc_ts,
        config_hash=config_hash,
    )

    # 4. Surface fits per (underlying, maturity); keep the rich SliceFit for the join.
    slice_fits, surface_params, surface_cells = _build_surfaces(
        iv_points,
        masters_by_key,
        as_of_date,
        as_of=as_of,
        calc_ts=calc_ts,
        config_hash=config_hash,
        surface=config.surface,
        moneyness_buckets=moneyness_buckets,
    )

    # 5. Risk and scenarios over the resolved valuation inputs.
    pricings, risk_aggregates, scenarios = _build_risk(
        positions,
        batch=batch,
        forwards=forward_estimates,
        slices=slice_fits,
        masters={master.instrument_key: master for master in masters},
        config=config,
        config_hash=config_hash,
        as_of=as_of,
        calc_ts=calc_ts,
        exercise_style_for=exercise_style_for,
    )

    return ActorOutputs(
        snapshots=batch.snapshots,
        forwards=tuple(forward_points),
        iv_points=tuple(iv_points),
        surface_parameters=tuple(surface_params),
        surface_grid=tuple(surface_cells),
        pricings=tuple(pricings),
        risk_aggregates=tuple(risk_aggregates),
        scenarios=tuple(scenarios),
    )


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
    config_hash: str,
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
                config_hash=config_hash,
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
    config_hash: str,
) -> list[IvPoint]:
    """Solve and project an IvPoint per usable, converged option quote.

    Uses the maturity's usable forward (so ``k`` and the discount factor match what
    fed the forward); an unconverged solve is labeled by the solver and simply not
    emitted (``iv_point`` would reject it). Output sorted by (underlying, maturity,
    strike, right) for a deterministic order.
    """
    forward_by_key = {
        (estimate.underlying, round(estimate.maturity_years, _MATURITY_MATCH_DECIMALS)): estimate
        for estimate in forwards
        if estimate.is_usable
    }
    rows: list[tuple[tuple[str, float, float, str], IvPoint]] = []
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
        if not result.converged:
            continue
        point = iv_point(
            result,
            snapshot_ts=as_of,
            source_snapshot_ts=as_of,
            calc_ts=calc_ts,
            config_hash=config_hash,
        )
        sort_key = (instrument.underlying_symbol, maturity_years, instrument.strike, right)
        rows.append((sort_key, point))
    rows.sort(key=lambda row: row[0])
    return [point for _key, point in rows]


def _build_surfaces(
    iv_points: Sequence[IvPoint],
    masters: dict[str, InstrumentKey],
    as_of_date: date,
    *,
    as_of: datetime,
    calc_ts: datetime,
    config_hash: str,
    surface: SurfaceConfig,
    moneyness_buckets: tuple[float, ...],
) -> tuple[list[SliceFit], list[SurfaceParameters], list[SurfaceGrid]]:
    """Fit a slice per (underlying, maturity); keep the rich fit, project the curves.

    The rich :class:`SliceFit` is kept for every maturity (the join reads it). Projecting
    a fit into the persisted ``surface_parameters`` / grid cells is delegated to
    :func:`surfaces.project_surface_fit`, which owns the rule about which fit method emits
    which contract — an ``insufficient`` slice projects nothing. Outputs are sorted by
    (underlying, maturity) for determinism.
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
    params: list[SurfaceParameters] = []
    cells: list[SurfaceGrid] = []
    for (underlying, maturity_years, expiry) in sorted(by_maturity, key=lambda k: (k[0], k[1])):
        points = tuple(by_maturity[(underlying, maturity_years, expiry)])
        fit = fit_slice(
            underlying, maturity_years, points,
            expiry_date=expiry, day_count=DAY_COUNT, config=surface,
        )
        slice_fits.append(fit)
        projection = project_surface_fit(
            fit,
            moneyness_buckets,
            snapshot_ts=as_of,
            source_snapshot_ts=as_of,
            calc_ts=calc_ts,
            config_hash=config_hash,
        )
        if projection.parameters is not None:
            params.append(projection.parameters)
        cells.extend(projection.grid_cells)
    return slice_fits, params, cells


def _build_risk(
    positions: Sequence[Position],
    *,
    batch: SnapshotBatch,
    forwards: Sequence[ForwardEstimate],
    slices: Sequence[SliceFit],
    masters: dict[str, InstrumentMaster],
    config: PlatformConfig,
    config_hash: str,
    as_of: datetime,
    calc_ts: datetime,
    exercise_style_for: Callable[[InstrumentKey], str],
) -> tuple[list[PricingResult], list[RiskAggregate], list[ScenarioResult]]:
    """Resolve valuation inputs, then run the D risk and scenario pipelines.

    Returns empty tuples when there are no positions (the risk tuples are empty, not a
    partial object). The valuation join is the only place C's contracts meet D's input;
    everything after is D's pure math plus the actor's stamp.
    """
    if not positions:
        return [], [], []

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
        _pricing_for_line(line, as_of=as_of, calc_ts=calc_ts, config_hash=config_hash)
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
                config_hash=config_hash,
                sources=_risk_sources(net.lines, as_of),
            ),
        )
        for net in aggregate_lines(
            netted, portfolio_id=_portfolio_of(positions), dimension=_AGGREGATE_DIMENSION
        )
    ]

    grid = scenario_grid(config.scenario)
    scenario_version = effective_scenario_version(config.scenario)
    scenarios = [
        scenario_result(
            cell,
            valuation_ts=as_of,
            scenario_version=scenario_version,
            source_snapshot_ts=as_of,
            provenance=build_stamp(
                calc_ts=calc_ts,
                code_version=RISK_ENGINE_VERSION,
                config_hash=config_hash,
                sources=_risk_sources((cell.line,), as_of),
            ),
        )
        for cell in scenario_line_pnls(netted, grid)
    ]
    return pricings, aggregates, scenarios


def _pricing_for_line(
    line: PositionRisk, *, as_of: datetime, calc_ts: datetime, config_hash: str
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
        config_hash=config_hash,
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
    config_hash: str,
    as_of: datetime,
    calc_ts: datetime,
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style,
    moneyness_buckets: tuple[float, ...] = DEFAULT_MONEYNESS_BUCKETS,
    correlation_id: str = "",
    persist: bool = True,
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
        config_hash=config_hash,
        as_of=as_of,
        calc_ts=calc_ts,
        exercise_style_for=exercise_style_for,
        moneyness_buckets=moneyness_buckets,
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
