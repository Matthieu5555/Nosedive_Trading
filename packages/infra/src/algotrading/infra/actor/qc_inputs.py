"""The in-memory analytics intermediates the live QC plane needs — never persisted.

:func:`actor.driver.run_analytics` builds a chain of rich domain objects on its way to
the persisted :class:`actor.outputs.ActorOutputs` — the per-maturity
:class:`forwards.ForwardEstimate`, every :class:`iv.IvResult` (including the *non*-converged
ones the persisted ``iv_points`` deliberately drop), the rich per-slice
:class:`surfaces.SliceFit`, the netted :class:`risk.PositionRisk` lines, the
:class:`snapshots.SnapshotBatch`, and the scenario grid the stress reprice ran over — and
then discards them. Most named QC checks (``check_forward_stability``,
``check_parity_residual``, ``check_iv_solver_convergence``, ``check_calendar_sanity``,
``check_greek_sanity``, ``check_scenario_completeness``, ``check_underlying_quote_health``,
``check_option_chain_coverage``) read exactly those objects, so without a handle on them a
live End-of-Day run can only run the two grid checks plus the diagnostics-only surface-fit
check — the rest stay dark.

:class:`QcInputs` is that handle. It is the *full* intermediate set, carried beside (never
inside) :class:`ActorOutputs`, so the byte-identical-replay handle is unchanged: nothing
here is persisted, serialized into any contract table, or stamped into a manifest. In
particular :attr:`iv_results` carries the complete solver output — converged and
non-converged alike — so ``check_iv_solver_convergence`` can compute an honest
non-convergence ratio while the persisted ``iv_points`` stay exactly the converged subset.

It is a value, like everything else the actor returns: a frozen dataclass of frozen domain
objects, equal under ``==`` for equal inputs, so a replay reproduces it and a test can
assert over it directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from algotrading.infra.forwards import ForwardEstimate, ParityLine
from algotrading.infra.iv import IvResult
from algotrading.infra.risk import PositionRisk, Scenario
from algotrading.infra.snapshots import SnapshotBatch
from algotrading.infra.surfaces import CalendarViolation, SliceFit


@dataclass(frozen=True, slots=True)
class QcInputs:
    """Every rich analytics intermediate the named QC checks consume — in memory only.

    Carried beside :class:`actor.outputs.ActorOutputs`, not within it: this is *not* part of
    the byte-identical replay handle and is never persisted. Each field is the genuine object
    the actor computed (no fabricated compute-bearing values), so a check run over it scores
    the real run.

    Fields, and the check each feeds:

    * :attr:`batch` — the snapshot batch (``check_underlying_quote_health``,
      ``check_option_chain_coverage``).
    * :attr:`underlying_keys` — the bare-underlying snapshot keys, the anchor quotes
      ``check_underlying_quote_health`` judges (distinct from the option legs).
    * :attr:`expected_chain_keys` — the expected option-chain contract keys per underlying
      (from the instrument masters), the membership ``check_option_chain_coverage`` compares
      the usable chain against.
    * :attr:`forward_estimates` — the rich per-maturity forward estimates
      (``check_forward_stability``; also the parity-residual source via :attr:`parity_lines`).
    * :attr:`parity_lines` — one ``(underlying, maturity_years, ParityLine)`` per usable
      forward, the residual line ``check_parity_residual`` scores. The line carries the
      estimate's genuine ``forward``/``discount_factor`` (and the line's ``intercept``/``slope``
      that are their exact inverse) and the residuals already on the estimate's strike points,
      so no parity refit happens here.
    * :attr:`iv_results` — the **full** solver output, grouped by underlying, for every usable
      option quote — including the non-converged ones the persisted ``iv_points`` drop — so
      ``check_iv_solver_convergence`` can compute a per-underlying non-convergence ratio against
      a named target.
    * :attr:`slice_fits` — the rich per-slice fits (``check_surface_fit_error`` over the real
      fit, not the diagnostics-only rebuild).
    * :attr:`calendar_violations` — the calendar no-arb violations per underlying, precomputed
      in the actor over the run's own log-moneyness grid (``check_calendar_sanity``).
    * :attr:`risk_lines` — the netted position-risk lines (``check_greek_sanity``; also the
      contract set for scenario completeness).
    * :attr:`scenario_grid` — the combined scenario grid the stress reprice ran over, so the
      expected ``(scenario_id, contract_key)`` cartesian for ``check_scenario_completeness``
      is the grid the actor actually priced, not a re-derived one.
    * :attr:`portfolio_id` — the single portfolio the positions belong to (the
      ``check_scenario_completeness`` target), or ``""`` when there are no positions.

    Every field defaults empty so a run with no positions, no forwards, or no surfaces still
    yields a well-formed bundle, never a partial object.
    """

    batch: SnapshotBatch | None = None
    underlying_keys: tuple[str, ...] = field(default_factory=tuple)
    expected_chain_keys: tuple[tuple[str, tuple[str, ...]], ...] = field(default_factory=tuple)
    forward_estimates: tuple[ForwardEstimate, ...] = field(default_factory=tuple)
    parity_lines: tuple[tuple[str, float, ParityLine], ...] = field(default_factory=tuple)
    iv_results: tuple[tuple[str, tuple[IvResult, ...]], ...] = field(default_factory=tuple)
    slice_fits: tuple[SliceFit, ...] = field(default_factory=tuple)
    calendar_violations: tuple[tuple[str, tuple[CalendarViolation, ...]], ...] = field(
        default_factory=tuple
    )
    risk_lines: tuple[PositionRisk, ...] = field(default_factory=tuple)
    scenario_grid: tuple[Scenario, ...] = field(default_factory=tuple)
    portfolio_id: str = ""
