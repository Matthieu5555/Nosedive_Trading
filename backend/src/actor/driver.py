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

from config import PlatformConfig
from contracts import InstrumentKey, InstrumentMaster, Position, RawMarketEvent
from storage import ParquetStore

from .outputs import ActorOutputs
from .valuation_join import default_exercise_style

_LOGGER = structlog.get_logger("actor")

# Default moneyness buckets for the regularized surface grid, in log-moneyness.
# At-the-money-centered and symmetric so the persisted grid is comparable across
# underlyings; overridable per run.
DEFAULT_MONEYNESS_BUCKETS: tuple[float, ...] = (-0.2, -0.1, 0.0, 0.1, 0.2)


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
    raise NotImplementedError(
        "actor compute pipeline — implemented by Workstream E wave-1 (S1) against this frozen seam"
    )


def persist_outputs(store: ParquetStore, outputs: ActorOutputs) -> None:
    """Write every non-empty output tuple to its contract table, validated by A.

    Routes each contract to its table via ``contracts.table_for_contract`` and writes
    through ``store.write`` (write-ahead validation, all-or-nothing per table). The
    derived tables are replace-semantics, so re-persisting a recomputed as-of replaces
    just those partitions and never touches the append-only raw layer. Idempotent for
    a fixed :class:`ActorOutputs`: persisting the same outputs twice leaves identical
    partition bytes.
    """
    raise NotImplementedError(
        "actor persistence — implemented by Workstream E wave-1 (S1) against this frozen seam"
    )


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
    raise NotImplementedError(
        "actor day driver — implemented by Workstream E wave-1 (S1) against this frozen seam"
    )
