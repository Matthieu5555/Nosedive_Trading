"""Store-backed serving: the operator routes fed from the real pipeline tables.

This module reads only the ``algotrading.infra`` seams — `ParquetStore` for the
persisted contract tables, the frozen pricing seam (`price`/`PricingState` via the
risk engine), and the M3 risk functions for live scenario repricing. It projects
those into the same response dataclasses the fixture path serves (`data.py`), so
the HTTP contract is identical and the web app needs no knowledge of the source;
the per-response provenance (`provider="store"` plus the pipeline's real stamp)
is what tells an operator they are looking at pipeline output.

Two deliberate modeling assumptions, both visible here rather than buried:

* The discount factor is rebuilt as ``spot / forward`` (no-dividend spot-forward
  parity) because `ForwardCurvePoint` persists the forward but not the discount
  factor. Exact for the sample chain; revisit when a dividend-paying capture lands.
* Exercise style defaults to European: the instrument master carries no style
  field yet. The sample chain is European; an American capture will need the field.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import tables
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.risk import (
    ContractValuationInput,
    Scenario,
    full_reprice_pnl,
    position_risk,
    shock_valuation,
)
from algotrading.infra.risk.greeks import PositionRisk
from algotrading.infra.storage import ParquetStore

from .data import (
    ExpiryGreeks,
    GreekVector,
    MarketDashboard,
    OptionQuote,
    Provenance,
    ScenarioGridPoint,
    ScenarioInput,
    ScenarioResult,
    SnapshotQuote,
    SpotLadderPoint,
    UnderlyingChoice,
    VolatilitySurface,
    VolSurfacePoint,
    VolSurfaceSlice,
)

_PROVIDER = "store"
# Chart grids mirror the fixture path so the web pages render identically:
# the ladder's spot rungs (percent) and the spot x vol heatmap axes.
_LADDER_SPOT_SHOCKS = [-10.0, -7.5, -5.0, -2.5, 0.0, 2.5, 5.0, 7.5, 10.0]
_GRID_SPOT_SHOCKS = [-5.0, -2.5, 0.0, 2.5, 5.0]
_GRID_VOL_SHOCKS = [-4.0, 0.0, 4.0]
# Time rolls convert calendar days to years on the same basis the pipeline's
# day counts use for short maturities.
_DAYS_PER_YEAR = 365.0


class StoreDataError(Exception):
    """The store holds the underlying but a book line cannot be valued from it."""

    def __init__(self, contract_key: str, missing: str) -> None:
        self.contract_key = contract_key
        self.missing = missing
        super().__init__(f"cannot value {contract_key}: missing {missing}")


@dataclass(frozen=True)
class StoreDay:
    """One underlying's latest persisted day, indexed for serving."""

    underlying: str
    trade_date: date
    snapshot_ts: datetime
    spot: float
    currency: str
    underlying_snapshot: tables.MarketStateSnapshot
    option_snapshots: dict[str, tables.MarketStateSnapshot]
    instruments: dict[str, InstrumentKey]
    forwards_by_expiry: dict[date, tables.ForwardCurvePoint]
    iv_by_contract: dict[str, tables.IvPoint]
    surface_parameters: list[tables.SurfaceParameters]
    surface_grid: list[tables.SurfaceGrid]
    positions: list[tables.Position]
    stamp: ProvenanceStamp


def store_underlyings(store: ParquetStore) -> list[UnderlyingChoice]:
    """The underlyings actually present in the store, newest trade date first."""
    latest: dict[str, date] = {}
    for trade_date, underlying in store.list_partitions("market_state_snapshots"):
        if trade_date > latest.get(underlying, date.min):
            latest[underlying] = trade_date
    choices: list[UnderlyingChoice] = []
    for underlying in sorted(latest, key=lambda u: latest[u], reverse=True):
        day = load_store_day(store, underlying)
        if day is not None:
            choices.append(
                UnderlyingChoice(
                    symbol=underlying,
                    name=f"{underlying} (pipeline {day.trade_date.isoformat()})",
                    asset_class="equity",
                    currency=day.currency,
                )
            )
    return choices


def load_store_day(store: ParquetStore, underlying: str) -> StoreDay | None:
    """Load and index the latest persisted day for one underlying, or None."""
    symbol = underlying.upper()
    dates = [d for d, u in store.list_partitions("market_state_snapshots") if u == symbol]
    if not dates:
        return None
    trade_date = max(dates)
    snapshots = store.read("market_state_snapshots", trade_date=trade_date, underlying=symbol)
    masters = [
        master
        for master in store.read("instrument_master")
        if master.instrument.underlying_symbol == symbol
    ]
    instruments = {master.instrument_key: master.instrument for master in masters}
    underlying_snapshot = next(
        (s for s in snapshots if instruments.get(s.instrument_key, _NO_KEY).security_type != "OPT"),
        None,
    )
    if underlying_snapshot is None:
        return None
    option_snapshots = {
        s.instrument_key: s
        for s in snapshots
        if instruments.get(s.instrument_key, _NO_KEY).security_type == "OPT"
    }
    forwards = store.read("forward_curve", trade_date=trade_date, underlying=symbol)
    iv_points = store.read("iv_points", trade_date=trade_date, underlying=symbol)
    surface_parameters = store.read("surface_parameters", trade_date=trade_date, underlying=symbol)
    surface_grid = store.read("surface_grid", trade_date=trade_date, underlying=symbol)
    positions = [
        position
        for position in store.read("positions")
        if instruments.get(position.contract_key) is not None
    ]
    spot = underlying_snapshot.reference_spot
    stamp_owner = surface_parameters[0] if surface_parameters else underlying_snapshot
    underlying_instrument = instruments[underlying_snapshot.instrument_key]
    return StoreDay(
        underlying=symbol,
        trade_date=trade_date,
        snapshot_ts=underlying_snapshot.snapshot_ts,
        spot=spot,
        currency=underlying_instrument.currency,
        underlying_snapshot=underlying_snapshot,
        option_snapshots=option_snapshots,
        instruments=instruments,
        forwards_by_expiry={f.expiry_date: f for f in forwards},
        iv_by_contract={p.contract_key: p for p in iv_points},
        surface_parameters=sorted(surface_parameters, key=lambda s: s.maturity_years),
        surface_grid=surface_grid,
        positions=positions,
        stamp=stamp_owner.provenance,
    )


# Sentinel instrument so dict lookups stay total during snapshot classification.
_NO_KEY = InstrumentKey(
    underlying_symbol="", security_type="NONE", exchange="", currency="USD",
    multiplier=1.0, broker_contract_id="",
)


def market_dashboard_from_store(store: ParquetStore, underlying: str) -> MarketDashboard | None:
    """The market dashboard from the persisted tables; None when not in the store."""
    day = load_store_day(store, underlying)
    if day is None:
        return None
    chain = _option_chain(day)
    return MarketDashboard(
        underlying=UnderlyingChoice(
            symbol=day.underlying,
            name=f"{day.underlying} (pipeline {day.trade_date.isoformat()})",
            asset_class="equity",
            currency=day.currency,
        ),
        index_snapshot=_index_quote(day),
        stock_snapshots=[],  # no component-stock capture in the pipeline yet
        option_chain=chain,
        greek_totals=_sum_chain_greeks(chain),
        volatility_surface=_volatility_surface(day),
        provenance=_provenance(day, f"market:{day.underlying}@{day.trade_date.isoformat()}"),
    )


def scenario_from_store(store: ParquetStore, requested: ScenarioInput) -> ScenarioResult | None:
    """Run the requested scenario by full reprice over the persisted book.

    Returns None when the underlying (or any book) is absent from the store —
    the caller falls back to the fixture path. Raises :class:`StoreDataError`
    when the book exists but a line cannot be valued: a risk monitor must fail
    loudly rather than silently drop a position.
    """
    day = load_store_day(store, requested.underlying)
    if day is None or not day.positions:
        return None
    portfolio_id = day.positions[0].portfolio_id
    lines = [
        position_risk(
            portfolio_id=position.portfolio_id,
            quantity=position.quantity,
            valuation=_valuation_input(day, position.contract_key),
        )
        for position in day.positions
    ]
    scenario = _scenario(
        requested.spot_shock_percent, requested.vol_shock_points, requested.time_roll_days
    )
    pnl = sum(full_reprice_pnl(line, scenario) for line in lines)
    baseline_value = sum(line.market_value for line in lines)
    greek_before = _net_greeks(lines)
    greek_after = _net_greeks(_shocked_lines(lines, scenario))
    scenario_id = _stable_id(
        f"{day.underlying}:{portfolio_id}:{requested.spot_shock_percent}:"
        f"{requested.vol_shock_points}:{requested.time_roll_days}:{day.stamp.stamp_hash}"
    )
    return ScenarioResult(
        scenario_id=scenario_id,
        requested=replace(requested, underlying=day.underlying, portfolio_id=portfolio_id),
        baseline_value=round(baseline_value, 2),
        shocked_value=round(baseline_value + pnl, 2),
        pnl=round(pnl, 2),
        greek_before=greek_before,
        greek_after=greek_after,
        grid=_scenario_grid(lines),
        ladder=_spot_ladder(lines, requested.vol_shock_points, requested.time_roll_days),
        expiry_buckets=_expiry_buckets(day, lines),
        provenance=_provenance(day, f"scenario:{scenario_id}"),
    )


def _scenario(spot_percent: float, vol_points: float, roll_days: float) -> Scenario:
    """API shock units (percent / vol points / days) → the risk engine's fractions."""
    return Scenario(
        scenario_id=f"api:{spot_percent}:{vol_points}:{roll_days}",
        family="api",
        spot_shock=spot_percent / 100.0,
        vol_shock=vol_points / 100.0,
        time_shock=roll_days / _DAYS_PER_YEAR,
    )


def _valuation_input(day: StoreDay, contract_key: str) -> ContractValuationInput:
    instrument = day.instruments.get(contract_key)
    if instrument is None or instrument.expiry is None or instrument.strike is None:
        raise StoreDataError(contract_key, "instrument master")
    forward_point = day.forwards_by_expiry.get(instrument.expiry)
    if forward_point is None:
        raise StoreDataError(contract_key, f"forward for expiry {instrument.expiry}")
    iv_point = day.iv_by_contract.get(contract_key)
    if iv_point is None:
        raise StoreDataError(contract_key, "solved implied vol")
    maturity = forward_point.maturity_years
    forward = forward_point.forward
    # No-dividend spot-forward parity (module docstring): DF = S / F, and carry
    # follows so the valuation's derived forward reproduces the persisted one.
    discount_factor = day.spot / forward
    carry = math.log(forward / day.spot) / maturity
    return ContractValuationInput(
        contract_key=contract_key,
        underlying=day.underlying,
        option_right=instrument.option_right or "C",
        exercise_style="european",
        strike=instrument.strike,
        maturity_years=maturity,
        spot=day.spot,
        carry=carry,
        volatility=iv_point.iv,
        discount_factor=discount_factor,
        multiplier=instrument.multiplier,
        currency=instrument.currency,
    )


def _shocked_lines(lines: list[PositionRisk], scenario: Scenario) -> list[PositionRisk]:
    return [
        position_risk(
            portfolio_id=line.portfolio_id,
            quantity=line.quantity,
            valuation=shock_valuation(line.valuation, scenario),
        )
        for line in lines
    ]


def _net_greeks(lines: list[PositionRisk]) -> GreekVector:
    return GreekVector(
        delta=round(sum(line.position_delta for line in lines), 4),
        gamma=round(sum(line.position_gamma for line in lines), 5),
        vega=round(sum(line.position_vega for line in lines), 4),
        theta=round(sum(line.position_theta for line in lines), 4),
        rho=round(sum(line.greeks.rho * line.scale for line in lines), 4),
    )


def _spot_ladder(
    lines: list[PositionRisk], vol_points: float, roll_days: float
) -> list[SpotLadderPoint]:
    points: list[SpotLadderPoint] = []
    for spot_percent in _LADDER_SPOT_SHOCKS:
        scenario = _scenario(spot_percent, vol_points, roll_days)
        shocked = _net_greeks(_shocked_lines(lines, scenario))
        points.append(
            SpotLadderPoint(
                spot_shock_percent=spot_percent,
                pnl=round(sum(full_reprice_pnl(line, scenario) for line in lines), 2),
                delta=shocked.delta,
                gamma=shocked.gamma,
                vega=shocked.vega,
                theta=shocked.theta,
            )
        )
    return points


def _scenario_grid(lines: list[PositionRisk]) -> list[ScenarioGridPoint]:
    points: list[ScenarioGridPoint] = []
    for spot_percent in _GRID_SPOT_SHOCKS:
        for vol_points in _GRID_VOL_SHOCKS:
            scenario = _scenario(spot_percent, vol_points, 0)
            shocked = _net_greeks(_shocked_lines(lines, scenario))
            points.append(
                ScenarioGridPoint(
                    spot_shock_percent=spot_percent,
                    vol_shock_points=vol_points,
                    pnl=round(sum(full_reprice_pnl(line, scenario) for line in lines), 2),
                    delta_after=shocked.delta,
                    vega_after=shocked.vega,
                )
            )
    return points


def _expiry_buckets(day: StoreDay, lines: list[PositionRisk]) -> list[ExpiryGreeks]:
    grouped: dict[date, list[PositionRisk]] = {}
    for line in lines:
        instrument = day.instruments[line.contract_key]
        assert instrument.expiry is not None  # _valuation_input already enforced it
        grouped.setdefault(instrument.expiry, []).append(line)
    return [
        ExpiryGreeks(expiry=expiry, contracts=len(bucket), greeks=_net_greeks(bucket))
        for expiry, bucket in sorted(grouped.items())
    ]


def _option_chain(day: StoreDay) -> list[OptionQuote]:
    quotes: list[OptionQuote] = []
    for contract_key, snapshot in day.option_snapshots.items():
        instrument = day.instruments[contract_key]
        iv_point = day.iv_by_contract.get(contract_key)
        if iv_point is None or instrument.expiry is None or instrument.strike is None:
            continue  # only contracts with a solved vol are chartable
        valuation = _valuation_input(day, contract_key)
        greeks = position_risk(portfolio_id="chain", quantity=1.0, valuation=valuation).greeks
        mid = snapshot.last
        quotes.append(
            OptionQuote(
                contract_key=contract_key,
                underlying=day.underlying,
                expiry=instrument.expiry,
                strike=instrument.strike,
                option_type="call" if instrument.option_right == "C" else "put",
                bid=round(snapshot.bid, 4),
                ask=round(snapshot.ask, 4),
                mid=round(mid, 4),
                implied_vol=round(iv_point.iv, 6),
                open_interest=0,  # not captured by the pipeline
                volume=0,  # not captured by the pipeline
                greeks=GreekVector(
                    delta=round(greeks.delta, 4),
                    gamma=round(greeks.gamma, 5),
                    vega=round(greeks.vega, 4),
                    theta=round(greeks.theta, 4),
                    rho=round(greeks.rho, 4),
                ),
            )
        )
    quotes.sort(key=lambda q: (q.expiry, q.strike, q.option_type))
    return quotes


def _sum_chain_greeks(chain: list[OptionQuote]) -> GreekVector:
    return GreekVector(
        delta=round(sum(q.greeks.delta for q in chain), 4),
        gamma=round(sum(q.greeks.gamma for q in chain), 5),
        vega=round(sum(q.greeks.vega for q in chain), 4),
        theta=round(sum(q.greeks.theta for q in chain), 4),
        rho=round(sum(q.greeks.rho for q in chain), 4),
    )


def _index_quote(day: StoreDay) -> SnapshotQuote:
    snapshot = day.underlying_snapshot
    return SnapshotQuote(
        symbol=day.underlying,
        name=f"{day.underlying} (pipeline)",
        last=round(snapshot.last, 4),
        bid=round(snapshot.bid, 4),
        ask=round(snapshot.ask, 4),
        change_percent=0.0,  # one persisted day: no prior close to move against
        volume=0,  # not captured by the pipeline
        snapshot_ts=snapshot.snapshot_ts,
        currency=day.currency,
    )


def _volatility_surface(day: StoreDay) -> VolatilitySurface:
    slices = [
        VolSurfaceSlice(
            maturity_years=row.maturity_years,
            expiry=row.expiry_date,
            atm_vol=round(_svi_vol(row, 0.0), 6),
            skew_25_delta=round((_svi_vol(row, -0.1) - _svi_vol(row, 0.1)) / 2.0, 6),
            svi_a=row.svi_a,
            svi_b=row.svi_b,
            svi_rho=row.svi_rho,
            svi_m=row.svi_m,
            svi_sigma=row.svi_sigma,
            rmse=row.diagnostics.rmse,
            n_points=row.diagnostics.n_points,
        )
        for row in day.surface_parameters
    ]
    points = [
        VolSurfacePoint(
            log_moneyness=cell.moneyness_bucket,
            maturity_years=cell.maturity_years,
            implied_vol=round(math.sqrt(cell.total_variance / cell.maturity_years), 6),
            total_variance=cell.total_variance,
        )
        for cell in sorted(day.surface_grid, key=lambda c: (c.maturity_years, c.moneyness_bucket))
        if cell.maturity_years > 0
    ]
    return VolatilitySurface(
        underlying=day.underlying, as_of=day.snapshot_ts, slices=slices, points=points
    )


def _svi_vol(row: tables.SurfaceParameters, log_moneyness: float) -> float:
    """Implied vol at one log-moneyness from a raw-SVI total-variance slice."""
    k = log_moneyness - row.svi_m
    total_variance = row.svi_a + row.svi_b * (
        row.svi_rho * k + math.sqrt(k * k + row.svi_sigma * row.svi_sigma)
    )
    return math.sqrt(max(total_variance, 0.0) / row.maturity_years)


def _provenance(day: StoreDay, source: str) -> Provenance:
    return Provenance(
        as_of=day.stamp.calc_ts,
        provider=_PROVIDER,
        code_version=day.stamp.code_version,
        config_hash=day.stamp.config_hash,
        source=source,
        stamp_hash=day.stamp.stamp_hash,
    )


def _stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def default_data_root() -> Path:
    """``<repo root>/data`` — the root is found by the AGENTS.md marker walk."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "AGENTS.md").exists():
            return parent / "data"
    return Path.cwd() / "data"
