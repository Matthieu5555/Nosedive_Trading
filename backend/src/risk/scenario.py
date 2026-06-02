"""Scenario / stress engine (roadmap step 12): explicit market states, full reprice.

A scenario is an explicit shocked market *state*, never a Greek multiplier. The
full reprice — reprice every position under the shocked state and difference
against base — is the source of truth; the local Taylor approximation (Eq 19) is a
fast convenience that must agree with it for small shocks and is expected to
diverge for large ones (``tasks/04-risk-engine.md``). Both are offered; the full
reprice is what lands in :class:`contracts.ScenarioResult`.

The grid is built deterministically from A's versioned :class:`config.ScenarioConfig`
(its ``spot_shocks`` and ``vol_shocks``) plus two D-owned, documented construction
rules — a combined spot-and-vol crash and a small time roll-down — so that, given a
scenario version, the grid regenerates exactly. The version persisted on every
result is :func:`effective_scenario_version` — the config section version folded
with a hash of those D-owned construction rules — *not* ``config.version`` alone,
which would let two different grids share one version (see ADR 0006).

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
import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from config import ScenarioConfig
from contracts import ScenarioResult
from pricing import PriceGreeks, price
from provenance import ProvenanceStamp

from .bumps import DEFAULT_BUMPS, BumpSpec
from .greeks import PositionRisk, central_difference_greeks, net_lots
from .valuation import ContractValuationInput, pricing_state_for

# Day-count for the time roll-down, and the roll-down set (in days). Fixed code
# keyed to the scenario version; a change here is a deliberate grid change and moves
# the effective scenario version (see ``effective_scenario_version``).
_DAYS_PER_YEAR = 365.0
ROLL_DOWN_DAYS = (1,)

# The D-owned grid-construction policy, versioned independently of A's config. Bump
# on any change to how the grid is built; it is hashed into the persisted scenario
# version so two different grids can never share one version.
GRID_CONSTRUCTION_VERSION = "grid-1.0.0"
_CRASH_RULE_TAG = "crash=min_spot+max_vol"


class ScenarioGridError(Exception):
    """The configured shocks produced a grid with colliding scenario ids."""


def _unique_preserving_order(values: tuple[float, ...]) -> tuple[float, ...]:
    """Drop duplicate shocks, keeping first-seen order — a deterministic de-dup.

    Duplicate configured shocks would otherwise mint duplicate scenario ids, which
    silently collapse cells in any id-keyed map and double-count a scenario in the
    worst-case total. De-duping at the source keeps the grid well-formed regardless
    of config hygiene.
    """
    seen: set[float] = set()
    unique: list[float] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return tuple(unique)


def _grid_construction_hash(
    roll_down_days: tuple[int, ...] = ROLL_DOWN_DAYS, crash_rule_tag: str = _CRASH_RULE_TAG
) -> str:
    """A short, stable hash of the D-owned grid-construction constants.

    Folded into the persisted scenario version, so changing ``ROLL_DOWN_DAYS`` or the
    crash rule moves the version automatically even when ``config.version`` does not.
    """
    payload = {
        "version": GRID_CONSTRUCTION_VERSION,
        "roll_down_days": list(roll_down_days),
        "crash_rule": crash_rule_tag,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def effective_scenario_version(config: ScenarioConfig) -> str:
    """The scenario version to persist on every result.

    Combines A's config section version with a hash of D's grid-construction
    constants, so a report regenerates exactly from positions + snapshot + this
    version: changing either the economic shocks (config) or the construction rules
    (``ROLL_DOWN_DAYS``, the crash rule) moves it. Persisting ``config.version`` alone
    would let two different grids share one version.
    """
    return f"{config.version}+{_grid_construction_hash()}"


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
    spot_shocks = _unique_preserving_order(config.spot_shocks)
    vol_shocks = _unique_preserving_order(config.vol_shocks)
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
        for days in ROLL_DOWN_DAYS
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


def _taylor_pnl(greeks: PriceGreeks, *, spot: float, scale: float, scenario: Scenario) -> float:
    """The local Greeks (Taylor, Eq 19) PnL for one line, given its Greeks."""
    d_spot = spot * scenario.spot_shock
    per_unit = (
        greeks.delta * d_spot
        + 0.5 * greeks.gamma * d_spot * d_spot
        + greeks.vega * scenario.vol_shock
        + greeks.theta * scenario.time_shock
    )
    return per_unit * scale


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
    its bump from the same versioned :data:`risk.bumps.DEFAULT_BUMPS` the Greeks
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
    """Portfolio full-reprice PnL totalled per scenario id, in insertion order."""
    totals: dict[str, float] = {}
    for cell in cells:
        totals[cell.scenario.scenario_id] = (
            totals.get(cell.scenario.scenario_id, 0.0) + cell.full_reprice_pnl
        )
    return totals


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
    totals = {sid: sum(c.full_reprice_pnl for c in cs) for sid, cs in by_scenario.items()}
    worst_sid = min(totals, key=lambda sid: totals[sid])
    contributors = tuple(
        sorted(by_scenario[worst_sid], key=lambda c: c.full_reprice_pnl)
    )
    return WorstCase(
        scenario=contributors[0].scenario,
        total_pnl=totals[worst_sid],
        contributors=contributors,
    )


def scenario_result(
    cell: ScenarioLinePnl,
    *,
    valuation_ts: datetime,
    scenario_version: str,
    source_snapshot_ts: datetime,
    provenance: ProvenanceStamp,
) -> ScenarioResult:
    """Project one full-reprice PnL cell into A's ``ScenarioResult`` contract.

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
        pnl=cell.full_reprice_pnl,
        scenario_version=scenario_version,
        source_snapshot_ts=source_snapshot_ts,
        provenance=provenance,
    )
