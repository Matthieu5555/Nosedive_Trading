"""Scenario / stress engine (roadmap step 12): explicit market states, full reprice.

A scenario is an explicit shocked market *state*, never a Greek multiplier
(``documentation/blueprint/05-math-notes.md`` §5: "Scenarios should be treated as
explicit market states, not just as Greek multipliers"). The full reprice — reprice
every position under the shocked state and difference against base — is the source of
truth; the local Taylor approximation (Eq 19) is a fast convenience that must agree
with it for small shocks and is expected to diverge for large ones. Both are offered;
the full reprice is what lands in :class:`algotrading.infra.contracts.ScenarioResult`.

The grid is built deterministically from the versioned
:class:`algotrading.core.config.ScenarioConfig` (its ``spot_shocks`` and ``vol_shocks``)
plus two documented construction rules — a combined spot-and-vol crash and a small time
roll-down — so that, given a scenario version, the grid regenerates exactly. The default
family the blueprint names is "parallel spot moves, parallel implied-volatility shifts, a
combined spot-and-vol stress, and a small time roll-down". The version persisted on every
result is :func:`effective_scenario_version` — the config section version folded with a
hash of the grid-construction rules — *not* ``config.version`` alone, which would let two
different grids share one version (this is the blueprint's "scenario grid should be
version-controlled and retrievable alongside every scenario result", made tamper-evident).

Sign conventions, stated once and asserted by tests:

* ``spot_shock`` is relative: ``new_spot = spot * (1 + spot_shock)``.
* ``vol_shock`` is additive in vol units: ``new_vol = vol + vol_shock``.
* ``time_shock`` is a roll-down in years: ``new_T = T - time_shock`` (and the
  discount factor rolls with it at the implied rate).
* The Taylor time term is ``theta * time_shock`` with the pricer's *calendar* theta
  (``dPrice/dt``, negative for a long option), so a roll-down loses time value —
  matching the full reprice in sign.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.config import ScenarioConfig
from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import ScenarioResult
from algotrading.infra.pricing import PriceGreeks, price

from .bumps import DEFAULT_BUMPS, BumpSpec
from .config import AttributionConfig
from .greeks import PositionRisk, central_difference_greeks, net_lots
from .grid_versioning import dedup_preserving_order, short_construction_hash
from .valuation import ContractValuationInput, pricing_state_for

# The blueprint-faithful (Eq-19) decomposition conventions: the default attribution
# config, used by the lumped Taylor path so the split and the lump share one home and one
# arithmetic. Bound once here rather than minted per call so it is a single shared object.
_EQ19_ATTRIBUTION = AttributionConfig.defaults()

# Day-count for the time roll-down (years↔days). A fixed calendar convention, not a
# tunable economic input; the configurable roll-down *set* lives in ScenarioConfig.
_DAYS_PER_YEAR = 365.0

# The grid-construction policy, versioned independently of the economic config. Bump
# on any change to how the grid is built; it is hashed into the persisted scenario
# version so two different grids can never share one version.
GRID_CONSTRUCTION_VERSION = "grid-1.0.0"
_CRASH_RULE_TAG = "crash=min_spot+max_vol"


class ScenarioGridError(Exception):
    """The configured shocks produced a grid with colliding scenario ids."""


def _grid_construction_hash(
    roll_down_days: tuple[int, ...], crash_rule_tag: str = _CRASH_RULE_TAG
) -> str:
    """A short, stable hash of the grid-construction constants.

    Folded into the persisted scenario version, so changing the configured
    ``roll_down_days`` or the crash rule moves the version automatically even when
    ``config.version`` does not. The encoding (canonical-JSON SHA-256, 12 hex chars)
    is the shared :func:`~.grid_versioning.short_construction_hash` — byte-identical
    to the inline copy it replaced.
    """
    payload = {
        "version": GRID_CONSTRUCTION_VERSION,
        "roll_down_days": list(roll_down_days),
        "crash_rule": crash_rule_tag,
    }
    return short_construction_hash(payload)


def effective_scenario_version(config: ScenarioConfig) -> str:
    """The scenario version to persist on every result.

    Combines the config section version with a hash of the grid-construction
    constants, so a report regenerates exactly from positions + snapshot + this
    version: changing either the economic shocks (config) or the construction rules
    (``config.roll_down_days``, the crash rule) moves it. Persisting ``config.version``
    alone would let two different grids share one version.
    """
    return f"{config.version}+{_grid_construction_hash(config.roll_down_days)}"


@dataclass(frozen=True, slots=True)
class Scenario:
    """One explicit market shock: a relative spot move, vol shift, and time roll."""

    scenario_id: str
    family: str
    spot_shock: float
    vol_shock: float
    time_shock: float


def scenario_grid(config: ScenarioConfig) -> tuple[Scenario, ...]:
    """Build the deterministic scenario grid from a versioned scenario config.

    Families: one parallel spot move per ``spot_shock``, one parallel vol shift per
    ``vol_shock``, one combined crash (the most adverse spot move with the largest
    vol spike), and a small time roll-down. Ordering is fixed and the ids are
    stable, so the grid is a pure function of the config.
    """
    spot_shocks = dedup_preserving_order(config.spot_shocks)
    vol_shocks = dedup_preserving_order(config.vol_shocks)
    scenarios: list[Scenario] = [
        Scenario(f"spot_{shock:+.4f}", "spot", shock, 0.0, 0.0) for shock in spot_shocks
    ]
    scenarios += [
        Scenario(f"vol_{shock:+.4f}", "vol", 0.0, shock, 0.0) for shock in vol_shocks
    ]
    if spot_shocks and vol_shocks:
        crash_spot = min(spot_shocks)
        crash_vol = max(vol_shocks)
        scenarios.append(
            Scenario(
                f"crash_spot{crash_spot:+.4f}_vol{crash_vol:+.4f}",
                "combined",
                crash_spot,
                crash_vol,
                0.0,
            )
        )
    scenarios += [
        Scenario(f"roll_{days}d", "time", 0.0, 0.0, days / _DAYS_PER_YEAR)
        for days in config.roll_down_days
    ]
    grid = tuple(scenarios)
    ids = [scenario.scenario_id for scenario in grid]
    if len(set(ids)) != len(ids):
        # Distinct shocks that format to the same id (a precision collision). De-dup
        # handles exact duplicates; this guards the rest loudly rather than letting a
        # cell silently collapse downstream.
        raise ScenarioGridError(f"scenario grid has colliding ids: {sorted(ids)}")
    return grid


def shock_valuation(
    valuation: ContractValuationInput, scenario: Scenario
) -> ContractValuationInput:
    """Apply a scenario to a valuation input, producing the shocked market state.

    Carry is held fixed (so the forward tracks the shocked spot), vol is floored at
    zero, and the discount factor rolls to the shortened maturity at the implied
    rate — the same state the pricer would see on that day in that market.
    """
    new_spot = valuation.spot * (1.0 + scenario.spot_shock)
    new_vol = max(valuation.volatility + scenario.vol_shock, 0.0)
    new_maturity = max(valuation.maturity_years - scenario.time_shock, 0.0)
    new_df = math.exp(-valuation.implied_rate * new_maturity)
    return dataclasses.replace(
        valuation,
        spot=new_spot,
        volatility=new_vol,
        maturity_years=new_maturity,
        discount_factor=new_df,
    )


def full_reprice_pnl(line: PositionRisk, scenario: Scenario, *, steps: int | None = None) -> float:
    """Monetized PnL of one line under one scenario, by full reprice (source of truth)."""
    shocked = shock_valuation(line.valuation, scenario)
    state = pricing_state_for(shocked)
    shocked_price = price(state, steps=steps).price if steps is not None else price(state).price
    return (shocked_price - line.greeks.price) * line.scale


@dataclass(frozen=True, slots=True)
class TaylorTerms:
    """The blueprint Eq-19 Taylor PnL split into its named, dollar, book-additive terms.

    Each field is the monetized (``scale = multiplier * quantity``) contribution of one
    Greek to the local PnL. The first-order/Eq-19 set —
    ``delta_pnl = Δ·dS·scale``, ``gamma_pnl = ½·Γ·dS²·scale``, ``vega_pnl = Vega·dσ·scale``,
    ``theta_pnl = Θ·dt·scale`` — extended (TARGET §2.5 / §7.2) with the rate and
    second-order cross/convexity terms ``rho_pnl = Rho·dr·scale``,
    ``vanna_pnl = Vanna·dS·dσ·scale`` and ``volga_pnl = ½·Volga·dσ²·scale``. A book's
    terms are the term-wise sum of its lines'. :attr:`total` is the lumped Taylor number:
    the split can never drift from the lump because the lump *is* this sum (one home for
    the arithmetic). The three second-order fields default to ``0.0`` so a legacy
    four-term construction is unchanged; a pure-spot scenario leaves them zero (no vol or
    rate move), which is why extending the split moves no existing pure-spot golden.
    """

    delta_pnl: float
    gamma_pnl: float
    vega_pnl: float
    theta_pnl: float
    rho_pnl: float = 0.0
    vanna_pnl: float = 0.0
    volga_pnl: float = 0.0

    @property
    def total(self) -> float:
        """The lumped local Taylor PnL — the sum of the named contributions (through Volga)."""
        return (
            self.delta_pnl
            + self.gamma_pnl
            + self.vega_pnl
            + self.theta_pnl
            + self.rho_pnl
            + self.vanna_pnl
            + self.volga_pnl
        )


def terms_from_move(
    greeks: PriceGreeks,
    *,
    scale: float,
    d_spot: float,
    d_vol: float,
    d_time: float,
    d_rate: float,
    config: AttributionConfig = _EQ19_ATTRIBUTION,
) -> TaylorTerms:
    """Factor a local Taylor PnL into named per-Greek dollar terms from an explicit move.

    The single home of the term arithmetic, shared by the scenario path
    (:func:`taylor_terms`, ``d_rate == 0`` — the grid holds rates fixed) and the realized
    day-over-day path (which supplies a real ``d_rate``). The move is already in absolute
    units: ``d_spot`` is a spot change, ``d_vol`` an absolute vol change, ``d_time`` a
    calendar-time roll in years, ``d_rate`` an absolute rate change (decimal). The two
    Eq-19 convention flags are *reporting normalisations on the decomposition only* (see
    :class:`AttributionConfig` / ADR 0038): ``one_pct`` divides the gamma term by 100; a
    252 day-count rescales the theta term by 365/252. They move that one term (and the
    residual reported against the full reprice), never the full reprice, which stays the
    oracle. The rate and second-order terms carry no such fork — they are the literal
    dollar Taylor contributions.
    """
    gamma_curvature = 0.5 * greeks.gamma * d_spot * d_spot
    if config.gamma_normalisation == "one_pct":
        gamma_curvature = gamma_curvature / 100.0
    theta_contribution = greeks.theta * d_time * (365.0 / config.theta_day_count)
    return TaylorTerms(
        delta_pnl=greeks.delta * d_spot * scale,
        gamma_pnl=gamma_curvature * scale,
        vega_pnl=greeks.vega * d_vol * scale,
        theta_pnl=theta_contribution * scale,
        rho_pnl=greeks.rho * d_rate * scale,
        vanna_pnl=greeks.vanna * d_spot * d_vol * scale,
        volga_pnl=0.5 * greeks.volga * d_vol * d_vol * scale,
    )


def taylor_terms(
    greeks: PriceGreeks,
    *,
    spot: float,
    scale: float,
    scenario: Scenario,
    config: AttributionConfig = _EQ19_ATTRIBUTION,
) -> TaylorTerms:
    """Factor the local Taylor PnL (Eq 19, extended) into named per-Greek contributions.

    Resolves the scenario into an explicit move and delegates to the one arithmetic home
    :func:`terms_from_move`, so the scenario split and the realized split cannot diverge.
    The scenario grid carries no rate shock, so the rate term is zero here; ``vanna_pnl``
    is non-zero only for a combined spot-and-vol scenario and ``volga_pnl`` only when the
    scenario moves vol. With the blueprint-faithful default config
    (:data:`_EQ19_ATTRIBUTION` — ``one_dollar`` gamma, 365-day theta) the first four terms
    reproduce the classic ``Δ·dS + ½Γ·dS² + Vega·dσ + Θ·dt`` to the dollar.
    """
    return terms_from_move(
        greeks,
        scale=scale,
        d_spot=spot * scenario.spot_shock,
        d_vol=scenario.vol_shock,
        d_time=scenario.time_shock,
        d_rate=0.0,
        config=config,
    )


def _taylor_pnl(greeks: PriceGreeks, *, spot: float, scale: float, scenario: Scenario) -> float:
    """The local Greeks (Taylor, Eq 19) PnL for one line, given its Greeks.

    Delegates to :func:`taylor_terms` with the blueprint-faithful default config so the
    lumped path and the by-Greek split share one arithmetic home (the refactor-equivalence
    invariant the 2C attribution rests on).
    """
    return taylor_terms(greeks, spot=spot, scale=scale, scenario=scenario).total


def local_approx_pnl(line: PositionRisk, scenario: Scenario) -> float:
    """Local Taylor PnL using the line's analytic Greeks (the fast intraday path)."""
    return _taylor_pnl(line.greeks, spot=line.valuation.spot, scale=line.scale, scenario=scenario)


def local_approx_pnl_fd(
    valuation: ContractValuationInput,
    *,
    quantity: float,
    scenario: Scenario,
    bumps: BumpSpec = DEFAULT_BUMPS,
) -> float:
    """Local Taylor PnL using *finite-difference* Greeks from the shared bump source.

    The fallback for instruments whose analytic Greeks are not trusted. It draws
    its bump from the same versioned :data:`bumps.DEFAULT_BUMPS` the Greeks
    cross-check uses, so the two cannot silently diverge — the bump-consistency
    test asserts exactly this.
    """
    greeks = central_difference_greeks(valuation, bumps=bumps)
    return _taylor_pnl(
        greeks, spot=valuation.spot, scale=valuation.multiplier * quantity, scenario=scenario
    )


@dataclass(frozen=True, slots=True)
class ScenarioLinePnl:
    """One line's PnL under one scenario: both the full reprice and the approximation."""

    scenario: Scenario
    line: PositionRisk
    full_reprice_pnl: float
    approx_pnl: float


@dataclass(frozen=True, slots=True)
class WorstCase:
    """The worst (most negative total PnL) scenario and its ranked contributors."""

    scenario: Scenario
    total_pnl: float
    contributors: tuple[ScenarioLinePnl, ...]


def scenario_line_pnls(
    lines: Iterable[PositionRisk], grid: Iterable[Scenario], *, steps: int | None = None
) -> list[ScenarioLinePnl]:
    """Every (line, scenario) PnL cell, in a deterministic order (scenario, then contract).

    The result has exactly ``len(grid) * len(net_lots(lines))`` cells — completeness
    is a property of the cartesian product, asserted by the no-missing-cells test.
    Same-contract lots are netted first, so each contract is one cell per scenario:
    ``ScenarioResult`` has no lot dimension, and duplicate lots would otherwise mint
    two cells with the same ``(scenario, contract)`` key in arrival order.
    """
    line_list = net_lots(lines)
    cells: list[ScenarioLinePnl] = []
    for scenario in grid:
        for line in line_list:
            cells.append(
                ScenarioLinePnl(
                    scenario=scenario,
                    line=line,
                    full_reprice_pnl=full_reprice_pnl(line, scenario, steps=steps),
                    approx_pnl=local_approx_pnl(line, scenario),
                )
            )
    return cells


def scenario_totals(cells: Iterable[ScenarioLinePnl]) -> dict[str, float]:
    """Portfolio full-reprice PnL totalled per scenario id, in insertion order.

    Each scenario's total is accumulated with :func:`math.fsum` over its cells'
    full-reprice PnLs, so the total is reorder-invariant — matching the rest of the
    engine (``net_lots``, ``taylor_terms``) and keeping the worst-case selection that
    reads these totals bit-stable across cell orderings.
    """
    by_scenario: dict[str, list[float]] = {}
    for cell in cells:
        by_scenario.setdefault(cell.scenario.scenario_id, []).append(cell.full_reprice_pnl)
    return {sid: math.fsum(pnls) for sid, pnls in by_scenario.items()}


def worst_case(cells: Iterable[ScenarioLinePnl]) -> WorstCase:
    """The scenario with the largest portfolio loss, plus its lines worst-first.

    Raises ``ValueError`` on an empty cell set: a worst case over nothing is a
    question with no answer, not a zero.
    """
    cell_list = list(cells)
    if not cell_list:
        raise ValueError("worst_case requires at least one scenario PnL cell")
    by_scenario: dict[str, list[ScenarioLinePnl]] = {}
    for cell in cell_list:
        by_scenario.setdefault(cell.scenario.scenario_id, []).append(cell)
    # fsum so each scenario total — and therefore the worst-case selection — is
    # reorder-invariant, matching scenario_totals and the rest of the engine.
    totals = {
        sid: math.fsum(c.full_reprice_pnl for c in cs) for sid, cs in by_scenario.items()
    }
    worst_sid = min(totals, key=lambda sid: totals[sid])
    contributors = tuple(
        sorted(by_scenario[worst_sid], key=lambda c: c.full_reprice_pnl)
    )
    return WorstCase(
        scenario=contributors[0].scenario,
        total_pnl=totals[worst_sid],
        contributors=contributors,
    )


# --- Report attribution surface (adopted from Vincent's blueprint-aligned report) ---
# The blueprint's ``risk/scenarios.py`` wants families "filtered by report type" and
# "top-contributor extraction in the core API". These build on the full-reprice cells
# above, so the report is a pure view over the same source of truth.


@dataclass(frozen=True, slots=True)
class FamilyAttribution:
    """The worst-case scenario (largest loss) within one scenario family."""

    family: str
    worst_scenario_id: str
    total_pnl: float


@dataclass(frozen=True, slots=True)
class UnderlyingAttribution:
    """Worst-case full-reprice PnL attributed to one underlying."""

    underlying: str
    total_pnl: float


@dataclass(frozen=True, slots=True)
class ScenarioReport:
    """A stress report over a grid: per-scenario totals, the worst case with its ranked
    contributors, per-underlying attribution of the worst case, per-family worst cases,
    and the scenario version that makes it regenerable.

    Everything here is derived from the full-reprice cells, so the report is a pure
    function of (lines, grid) — reproducible from snapshot + positions + scenario
    version (``documentation/blueprint/08-acceptance-tests.md`` §risk)."""

    scenario_version: str
    totals: tuple[tuple[str, float], ...]
    worst_case: WorstCase
    worst_case_by_underlying: tuple[UnderlyingAttribution, ...]
    by_family: tuple[FamilyAttribution, ...]


def _attribute_worst_by_underlying(
    worst: WorstCase,
) -> tuple[UnderlyingAttribution, ...]:
    # Collect per-underlying PnLs as lists, then fsum — reorder-invariant.
    buckets: dict[str, list[float]] = {}
    for cell in worst.contributors:
        underlying = cell.line.underlying
        buckets.setdefault(underlying, []).append(cell.full_reprice_pnl)
    return tuple(
        UnderlyingAttribution(underlying=underlying, total_pnl=math.fsum(pnls))
        for underlying, pnls in sorted(buckets.items())
    )


def _attribute_by_family(cells: list[ScenarioLinePnl]) -> tuple[FamilyAttribution, ...]:
    family_of: dict[str, str] = {}
    # Collect per-scenario PnLs as lists, then fsum — reorder-invariant.
    by_scenario: dict[str, list[float]] = {}
    for cell in cells:
        sid = cell.scenario.scenario_id
        family_of[sid] = cell.scenario.family
        by_scenario.setdefault(sid, []).append(cell.full_reprice_pnl)
    totals = {sid: math.fsum(pnls) for sid, pnls in by_scenario.items()}
    worst: dict[str, tuple[float, str]] = {}
    for sid, total in totals.items():
        family = family_of[sid]
        current = worst.get(family)
        # Ties broken by scenario id, so the worst-per-family is reproducible.
        if current is None or (total, sid) < (current[0], current[1]):
            worst[family] = (total, sid)
    return tuple(
        FamilyAttribution(family=family, worst_scenario_id=sid, total_pnl=total)
        for family, (total, sid) in sorted(worst.items())
    )


def build_scenario_report(
    lines: Iterable[PositionRisk],
    grid: Iterable[Scenario],
    *,
    scenario_version: str,
    steps: int | None = None,
) -> ScenarioReport:
    """Run the grid by full reprice and summarize it: totals, worst case, attribution.

    Worst case is the scenario with the largest loss; its contributors are the lines
    ordered worst first, so the loss is always traceable to the positions that drove
    it. ``scenario_version`` is the caller-supplied :func:`effective_scenario_version`,
    stored on the report so it regenerates exactly.
    """
    cells = scenario_line_pnls(lines, grid, steps=steps)
    worst = worst_case(cells)
    totals = scenario_totals(cells)
    return ScenarioReport(
        scenario_version=scenario_version,
        totals=tuple(totals.items()),
        worst_case=worst,
        worst_case_by_underlying=_attribute_worst_by_underlying(worst),
        by_family=_attribute_by_family(cells),
    )


def scenario_result(
    cell: ScenarioLinePnl,
    *,
    valuation_ts: datetime,
    scenario_version: str,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> ScenarioResult:
    """Project one full-reprice PnL cell into the frozen ``ScenarioResult`` contract.

    ``pnl`` is the full reprice (the source of truth), and ``scenario_version`` is
    the caller-supplied :func:`effective_scenario_version` (config version folded
    with the grid-construction hash), persisted so a report regenerates exactly from
    positions + snapshot + scenario version. The stamp is injected, never read from
    a clock here.
    """
    return ScenarioResult(
        valuation_ts=valuation_ts,
        portfolio_id=cell.line.portfolio_id,
        scenario_id=cell.scenario.scenario_id,
        contract_key=cell.line.contract_key,
        spot_shock=cell.scenario.spot_shock,
        vol_shock=cell.scenario.vol_shock,
        time_shock=cell.scenario.time_shock,
        scenario_pnl=cell.full_reprice_pnl,
        scenario_version=scenario_version,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )
