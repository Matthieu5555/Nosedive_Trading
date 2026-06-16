from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.config import NamedScenarioConfig, ScenarioConfig
from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import ScenarioResult
from algotrading.infra.pricing import PriceGreeks, price

from .basket import basket_variance
from .bumps import DEFAULT_BUMPS, BumpSpec
from .config import AttributionConfig
from .greeks import PositionRisk, central_difference_greeks, net_lots
from .grid_versioning import dedup_preserving_order, short_construction_hash
from .valuation import ContractValuationInput, pricing_state_for

_EQ19_ATTRIBUTION = AttributionConfig.defaults()

_DAYS_PER_YEAR = 365.0

GRID_CONSTRUCTION_VERSION = "grid-1.0.0"
_CRASH_RULE_TAG = "crash=min_spot+max_vol"


class ScenarioGridError(Exception):
    pass


def _named_scenario_payload(
    named_scenarios: tuple[NamedScenarioConfig, ...],
) -> list[dict[str, object]]:
    return [
        {
            "label": named.label,
            "spot_shock": named.spot_shock,
            "vol_shock": named.vol_shock,
            "rate_shock": named.rate_shock,
            "correlation_shock": named.correlation_shock,
        }
        for named in named_scenarios
    ]


def _grid_construction_hash(
    roll_down_days: tuple[int, ...],
    rate_shocks: tuple[float, ...] = (),
    correlation_shocks: tuple[float, ...] = (),
    named_scenarios: tuple[NamedScenarioConfig, ...] = (),
    crash_rule_tag: str = _CRASH_RULE_TAG,
) -> str:
    payload: dict[str, object] = {
        "version": GRID_CONSTRUCTION_VERSION,
        "roll_down_days": list(roll_down_days),
        "crash_rule": crash_rule_tag,
    }
    if rate_shocks:
        payload["rate_shocks"] = list(rate_shocks)
    if correlation_shocks:
        payload["correlation_shocks"] = list(correlation_shocks)
    if named_scenarios:
        payload["named_scenarios"] = _named_scenario_payload(named_scenarios)
    return short_construction_hash(payload)


def effective_scenario_version(config: ScenarioConfig) -> str:
    return (
        f"{config.version}+"
        + _grid_construction_hash(
            config.roll_down_days,
            config.rate_shocks,
            config.correlation_shocks,
            config.named_scenarios,
        )
    )


@dataclass(frozen=True, slots=True)
class Scenario:

    scenario_id: str
    family: str
    spot_shock: float
    vol_shock: float
    time_shock: float
    rate_shock: float = 0.0
    correlation_shock: float = 0.0


def scenario_grid(config: ScenarioConfig) -> tuple[Scenario, ...]:
    spot_shocks = dedup_preserving_order(config.spot_shocks)
    vol_shocks = dedup_preserving_order(config.vol_shocks)
    rate_shocks = dedup_preserving_order(config.rate_shocks)
    correlation_shocks = dedup_preserving_order(config.correlation_shocks)
    scenarios: list[Scenario] = [
        Scenario(f"spot_{shock:+.4f}", "spot", shock, 0.0, 0.0) for shock in spot_shocks
    ]
    scenarios += [
        Scenario(f"vol_{shock:+.4f}", "vol", 0.0, shock, 0.0) for shock in vol_shocks
    ]
    scenarios += [
        Scenario(f"rate_{shock:+.4f}", "rate", 0.0, 0.0, 0.0, shock) for shock in rate_shocks
    ]
    scenarios += [
        Scenario(f"corr_{shock:+.4f}", "correlation", 0.0, 0.0, 0.0, 0.0, shock)
        for shock in correlation_shocks
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
    scenarios += [
        Scenario(
            f"named_{named.label}",
            "named",
            named.spot_shock,
            named.vol_shock,
            0.0,
            named.rate_shock,
            named.correlation_shock,
        )
        for named in config.named_scenarios
    ]
    grid = tuple(scenarios)
    ids = [scenario.scenario_id for scenario in grid]
    if len(set(ids)) != len(ids):
        raise ScenarioGridError(f"scenario grid has colliding ids: {sorted(ids)}")
    return grid


def shock_valuation(
    valuation: ContractValuationInput, scenario: Scenario
) -> ContractValuationInput:
    new_spot = valuation.spot * (1.0 + scenario.spot_shock)
    new_vol = max(valuation.volatility + scenario.vol_shock, 0.0)
    new_maturity = max(valuation.maturity_years - scenario.time_shock, 0.0)
    new_rate = valuation.implied_rate + scenario.rate_shock
    new_df = math.exp(-new_rate * new_maturity)
    return dataclasses.replace(
        valuation,
        spot=new_spot,
        volatility=new_vol,
        maturity_years=new_maturity,
        discount_factor=new_df,
    )


def full_reprice_pnl(line: PositionRisk, scenario: Scenario, *, steps: int | None = None) -> float:
    shocked = shock_valuation(line.valuation, scenario)
    state = pricing_state_for(shocked)
    shocked_price = price(state, steps=steps).price if steps is not None else price(state).price
    return (shocked_price - line.greeks.price) * line.scale


@dataclass(frozen=True, slots=True)
class BasketCorrelationExposure:

    weights: tuple[float, ...]
    vols: tuple[float, ...]
    avg_correlation: float
    vol_sensitivity: float


def correlation_shock_pnl(exposure: BasketCorrelationExposure, scenario: Scenario) -> float:
    if scenario.correlation_shock == 0.0:
        return 0.0
    base = basket_variance(
        exposure.weights, exposure.vols, avg_correlation=exposure.avg_correlation
    )
    shocked = basket_variance(
        exposure.weights,
        exposure.vols,
        avg_correlation=exposure.avg_correlation + scenario.correlation_shock,
    )
    return (shocked.vol - base.vol) * exposure.vol_sensitivity


@dataclass(frozen=True, slots=True)
class TaylorTerms:

    delta_pnl: float
    gamma_pnl: float
    vega_pnl: float
    theta_pnl: float
    rho_pnl: float = 0.0
    vanna_pnl: float = 0.0
    volga_pnl: float = 0.0

    @property
    def total(self) -> float:
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
    return terms_from_move(
        greeks,
        scale=scale,
        d_spot=spot * scenario.spot_shock,
        d_vol=scenario.vol_shock,
        d_time=scenario.time_shock,
        d_rate=scenario.rate_shock,
        config=config,
    )


def _taylor_pnl(greeks: PriceGreeks, *, spot: float, scale: float, scenario: Scenario) -> float:
    return taylor_terms(greeks, spot=spot, scale=scale, scenario=scenario).total


def local_approx_pnl(line: PositionRisk, scenario: Scenario) -> float:
    return _taylor_pnl(line.greeks, spot=line.valuation.spot, scale=line.scale, scenario=scenario)


def local_approx_pnl_fd(
    valuation: ContractValuationInput,
    *,
    quantity: float,
    scenario: Scenario,
    bumps: BumpSpec = DEFAULT_BUMPS,
) -> float:
    greeks = central_difference_greeks(valuation, bumps=bumps)
    return _taylor_pnl(
        greeks, spot=valuation.spot, scale=valuation.multiplier * quantity, scenario=scenario
    )


@dataclass(frozen=True, slots=True)
class ScenarioLinePnl:

    scenario: Scenario
    line: PositionRisk
    full_reprice_pnl: float
    approx_pnl: float


@dataclass(frozen=True, slots=True)
class WorstCase:

    scenario: Scenario
    total_pnl: float
    contributors: tuple[ScenarioLinePnl, ...]


def scenario_line_pnls(
    lines: Iterable[PositionRisk], grid: Iterable[Scenario], *, steps: int | None = None
) -> list[ScenarioLinePnl]:
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
    by_scenario: dict[str, list[float]] = {}
    for cell in cells:
        by_scenario.setdefault(cell.scenario.scenario_id, []).append(cell.full_reprice_pnl)
    return {sid: math.fsum(pnls) for sid, pnls in by_scenario.items()}


def worst_case(cells: Iterable[ScenarioLinePnl]) -> WorstCase:
    cell_list = list(cells)
    if not cell_list:
        raise ValueError("worst_case requires at least one scenario PnL cell")
    by_scenario: dict[str, list[ScenarioLinePnl]] = {}
    for cell in cell_list:
        by_scenario.setdefault(cell.scenario.scenario_id, []).append(cell)
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


@dataclass(frozen=True, slots=True)
class FamilyAttribution:

    family: str
    worst_scenario_id: str
    total_pnl: float


@dataclass(frozen=True, slots=True)
class UnderlyingAttribution:

    underlying: str
    total_pnl: float


@dataclass(frozen=True, slots=True)
class ScenarioReport:

    scenario_version: str
    totals: tuple[tuple[str, float], ...]
    worst_case: WorstCase
    worst_case_by_underlying: tuple[UnderlyingAttribution, ...]
    by_family: tuple[FamilyAttribution, ...]


def _attribute_worst_by_underlying(
    worst: WorstCase,
) -> tuple[UnderlyingAttribution, ...]:
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
        rate_shock=cell.scenario.rate_shock,
    )
