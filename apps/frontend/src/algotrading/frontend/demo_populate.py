"""Populate the offline store with the synthetic ``pm-demo-book`` so the
Positions, Risk Scenarios and Reconciliation tabs render real numbers.

There are no real trades booked in this workspace, so the position/risk/recon
tables the BFF reads (``pricing_results``, ``risk_aggregates``,
``scenario_results``, ``broker_positions``/``broker_fills``/
``broker_cash_balances`` and the ``booking/fills.jsonl`` ledger) are empty and the
three tabs render blank. This module *invents* a plausible book — the vol-seller
in :mod:`algotrading.frontend.demo_book` — and runs the project's **own**, tested
compute over the banked analytics to fill those tables for a set of trade dates.

Nothing here is a new pricing/risk model. The book legs are synthetic; every
number written is produced by the real engine:

* legs resolve against the banked ``projected_option_analytics`` cells,
* each leg becomes a :class:`PositionRisk` via
  :func:`algotrading.frontend.basket_scenarios.reconstruct_valuation` +
  :func:`algotrading.infra.risk.position_risk` (the same bridge the on-demand
  basket-stress endpoint uses),
* pricing rows come from :func:`algotrading.infra.pricing.pricing_result`,
* aggregates from :func:`algotrading.infra.risk.aggregate_lines` +
  :func:`algotrading.infra.risk.risk_aggregate`,
* scenario cells from the engine's full-reprice (``shock_valuation`` + the
  pricer, DF clamped to the pricer's valid domain) wrapped in
  :func:`algotrading.infra.risk.scenario_result`, over the configured families
  and stress-surface grids.

The writer is **opt-in** and **append-on-top-of-empty**: the BFF only calls it
when ``DEMO_BOOK=1`` and only for tables that are currently empty for the demo
portfolio, so with the flag unset every endpoint keeps its graceful-empty
behaviour unchanged. Validate against a temp store first; never point it at the
canonical ``data/`` to "see if it works".
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from algotrading.core.config import PlatformConfig, load_platform_config
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.execution import Fill, JsonlFillsLedger
from algotrading.infra.contracts import (
    Basket,
    BrokerCashBalance,
    BrokerFill,
    BrokerPosition,
    PricingResult,
    ProjectedOptionAnalytics,
    RiskAggregate,
    ScenarioResult,
)
from algotrading.infra.pricing import from_spot, price, pricing_result
from algotrading.infra.risk import (
    PositionRisk,
    aggregate_lines,
    net_lots,
    position_risk,
    risk_aggregate,
    scenario_result,
)
from algotrading.infra.risk.multileg import (
    analytics_cell_key,
    index_rows_by_cell_and_side,
    resolve_cell_side,
)
from algotrading.infra.risk.scenarios import (
    Scenario,
    ScenarioLinePnl,
    effective_scenario_version,
    scenario_grid,
    shock_valuation,
)
from algotrading.infra.risk.stress_surface import (
    effective_surface_version,
    stress_surface_grid,
)
from algotrading.infra.risk.valuation import ContractValuationInput, pricing_state_for
from algotrading.infra.storage import ParquetStore

from .basket_scenarios import reconstruct_valuation
from .context import AppContext
from .demo_book import build_book

#: The synthetic portfolio every demo row is stamped with. The Risk tab filters
#: by ``portfolio_id``; the Positions/Reconciliation tabs key off the booked
#: fills. One id keeps all three views pointing at the same book.
DEMO_PORTFOLIO_ID = "pm-demo-book"
DEMO_ACCOUNT_ID = "DEMO-PM"

#: Env flag that turns demo population on. Unset/anything-but-"1" => no-op, real
#: store untouched, endpoints stay graceful-empty.
DEMO_FLAG = "DEMO_BOOK"

#: Contract-key multiplier (OESX point value). Used both in the synthesized
#: contract_key (positions_read parses it from field index 4) and as the option
#: multiplier handed to the valuation reconstruction, so the position book's
#: ``market_value = price * qty * multiplier`` is coherent with the priced legs.
_MULTIPLIER = 10.0
_CURRENCY = "EUR"
_EXCHANGE = "EUREX"
_SURFACE_SIDE = "combined"

_BOOKING_DIRNAME = "booking"
_FILLS_FILENAME = "fills.jsonl"

_CODE_VERSION = "demo-book-1.0.0"
_DAYS_PER_YEAR = 365.0

# The three banked SX5E closes the cockpit ships with.
DEFAULT_TRADE_DATES = (date(2026, 6, 15), date(2026, 6, 16), date(2026, 6, 17))


def demo_enabled() -> bool:
    """True iff the ``DEMO_BOOK`` flag is set to ``1``."""

    return os.environ.get(DEMO_FLAG, "") == "1"


@dataclass(frozen=True, slots=True)
class _ResolvedLeg:
    """One demo leg matched to a banked analytics cell + a concrete contract."""

    contract_key: str
    quantity: float
    row: ProjectedOptionAnalytics
    line: PositionRisk


@dataclass(frozen=True, slots=True)
class DemoWriteReport:
    """What a single-date populate run produced (for logging/tests)."""

    trade_date: date
    n_fills: int
    n_pricings: int
    n_aggregates: int
    n_scenarios: int
    n_broker_positions: int


def _cells_by_underlying(
    rows: Iterable[ProjectedOptionAnalytics],
) -> dict[str, set[tuple[str, str]]]:
    """Distinct ``(tenor_label, delta_band)`` cells per underlying, combined side.

    These are the cells :func:`demo_book.build_book` walks to pick legs, so the
    book never points at a cell the day did not capture.
    """

    cells: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        if row.surface_side != _SURFACE_SIDE:
            continue
        if row.tenor_label is None or row.delta_band is None:
            continue
        cells.setdefault(row.underlying, set()).add((row.tenor_label, row.delta_band))
    return cells


def _option_right(target_delta: float) -> str:
    return "C" if target_delta >= 0.0 else "P"


def _expiry_for(snapshot: datetime, maturity_years: float) -> date:
    return (snapshot + timedelta(days=round(maturity_years * _DAYS_PER_YEAR))).date()


def _contract_key(row: ProjectedOptionAnalytics) -> str:
    """A canonical 9-field option contract_key the positions reader can parse.

    Layout (``|``-joined): underlying, OPT, exchange, currency, multiplier,
    broker_id, expiry, strike, right. ``positions_read`` reads the multiplier
    from index 4 and strike/expiry/right from indices 6-8.
    """

    right = _option_right(row.target_delta)
    expiry = _expiry_for(row.snapshot_ts, row.maturity_years).isoformat()
    strike = f"{row.strike:.2f}"
    broker_id = f"o-{row.tenor_label}-{row.delta_band}-{right}"
    return "|".join(
        (
            row.underlying,
            "OPT",
            _EXCHANGE,
            _CURRENCY,
            f"{int(_MULTIPLIER)}",
            broker_id,
            expiry,
            strike,
            right,
        )
    )


def _broker_contract_id(row: ProjectedOptionAnalytics) -> str:
    right = _option_right(row.target_delta)
    return f"o-{row.tenor_label}-{row.delta_band}-{right}"


def _conid(contract_key: str) -> int:
    """Stable positive synthetic conid derived from the contract_key."""

    return abs(hash(contract_key)) % 1_000_000_000 + 1


def _resolve_legs(
    basket: Basket, analytics_rows: Sequence[ProjectedOptionAnalytics]
) -> list[_ResolvedLeg]:
    """Turn each demo leg into a priced :class:`PositionRisk` via the real engine.

    Unresolvable legs (no banked cell for the chosen tenor/band) are simply
    dropped, mirroring ``basket_stress``'s gap handling; the book stays coherent
    with whatever the day captured.
    """

    by_cell_side, ambiguous = index_rows_by_cell_and_side(analytics_rows)
    resolved: list[_ResolvedLeg] = []
    for leg in basket.legs:
        if leg.instrument_kind != "option":
            continue
        key = analytics_cell_key(leg.underlying, leg.tenor_label, leg.delta_band)
        row, reason = resolve_cell_side(
            by_cell_side, ambiguous, key=key, surface_side=leg.surface_side
        )
        if row is None:
            continue
        contract_key = _contract_key(row)
        valuation = reconstruct_valuation(row, multiplier=_MULTIPLIER, currency=_CURRENCY)
        valuation = _rekey(valuation, contract_key)
        line = position_risk(
            portfolio_id=DEMO_PORTFOLIO_ID, quantity=leg.quantity, valuation=valuation
        )
        resolved.append(
            _ResolvedLeg(
                contract_key=contract_key, quantity=leg.quantity, row=row, line=line
            )
        )
    return resolved


def _rekey(
    valuation: ContractValuationInput, contract_key: str
) -> ContractValuationInput:
    return dataclasses.replace(valuation, contract_key=contract_key)


def _prov(trade_date: date, calc_ts: datetime, code_version: str) -> ProvenanceStamp:
    return stamp(
        calc_ts=calc_ts,
        code_version=code_version,
        config_hashes={"cfg": "demo-book"},
        source_records=(source_ref("baskets", DEMO_PORTFOLIO_ID, trade_date.isoformat()),),
        source_timestamps=(calc_ts,),
    )


def _pricing_rows(
    legs: Sequence[_ResolvedLeg], *, as_of: datetime, prov: ProvenanceStamp
) -> list[PricingResult]:
    rows: list[PricingResult] = []
    for leg in legs:
        v = leg.line.valuation
        state = from_spot(
            spot=v.spot,
            strike=v.strike,
            maturity_years=v.maturity_years,
            volatility=v.volatility,
            discount_factor=v.discount_factor,
            option_right=v.option_right,
            carry=v.carry,
            exercise_style=v.exercise_style,
        )
        greeks = price(state)
        rows.append(
            pricing_result(
                state,
                greeks,
                snapshot_ts=as_of,
                contract_key=leg.contract_key,
                source_snapshot_ts=as_of,
                provenance=prov,
            )
        )
    return rows


def _aggregate_rows(
    lines: Sequence[PositionRisk], *, as_of: datetime, prov: ProvenanceStamp
) -> list[RiskAggregate]:
    return [
        risk_aggregate(net, valuation_ts=as_of, source_snapshot_ts=as_of, provenance=prov)
        for net in aggregate_lines(
            net_lots(lines), portfolio_id=DEMO_PORTFOLIO_ID, dimension="underlying"
        )
    ]


def _clamped_full_reprice_pnl(line: PositionRisk, scenario: Scenario) -> float:
    """Full-reprice one line under a scenario, clamping the shocked DF to (0, 1].

    Mirrors the engine's ``full_reprice_pnl`` but applies the same DF floor as
    ``basket_scenarios._rate_shock_pnl``. The demo legs are reconstructed
    rate-free (parity-implied DF approximately 1), so a downward rate shock would
    imply a sub-zero rate (DF > 1) the pricer rejects. Flooring DF at 1.0 is an
    honest "no further discount benefit" for that cell rather than a crash.
    """

    shocked = shock_valuation(line.valuation, scenario)
    if shocked.discount_factor > 1.0:
        shocked = dataclasses.replace(shocked, discount_factor=1.0)
    shocked_price = price(pricing_state_for(shocked)).price
    return (shocked_price - line.greeks.price) * line.scale


def _scenario_rows(
    lines: Sequence[PositionRisk],
    config: PlatformConfig,
    *,
    as_of: datetime,
    prov: ProvenanceStamp,
) -> list[ScenarioResult]:
    netted = net_lots(lines)
    rows: list[ScenarioResult] = []

    def _emit(grid: tuple[Scenario, ...], version: str) -> None:
        for scenario in grid:
            for line in netted:
                cell = ScenarioLinePnl(
                    scenario=scenario,
                    line=line,
                    full_reprice_pnl=_clamped_full_reprice_pnl(line, scenario),
                    approx_pnl=0.0,
                )
                rows.append(
                    scenario_result(
                        cell,
                        valuation_ts=as_of,
                        scenario_version=version,
                        source_snapshot_ts=as_of,
                        provenance=prov,
                    )
                )

    _emit(scenario_grid(config.scenario), effective_scenario_version(config.scenario))
    _emit(stress_surface_grid(config.scenario), effective_surface_version(config.scenario))
    return rows


def _fills_for(
    legs: Sequence[_ResolvedLeg], *, trade_date: date, as_of: datetime, prov: ProvenanceStamp
) -> list[Fill]:
    fills: list[Fill] = []
    for idx, leg in enumerate(legs):
        fills.append(
            Fill(
                fill_id=f"demo-{trade_date.isoformat()}-{idx}",
                booking_id=f"demo-bkg-{trade_date.isoformat()}",
                source_basket_id=DEMO_PORTFOLIO_ID,
                trade_date=trade_date,
                underlying=leg.row.underlying,
                contract_key=leg.contract_key,
                signed_qty=Decimal(str(leg.quantity)),
                price=leg.row.price,
                fill_ts=as_of,
                provenance=prov,
                broker_contract_id=_broker_contract_id(leg.row),
            )
        )
    return fills


def _broker_rows(
    legs: Sequence[_ResolvedLeg], *, as_of: datetime
) -> tuple[list[BrokerPosition], list[BrokerCashBalance], list[BrokerFill]]:
    positions: list[BrokerPosition] = []
    fills: list[BrokerFill] = []
    net_liq = 0.0
    for idx, leg in enumerate(legs):
        market_value = leg.row.price * leg.quantity * _MULTIPLIER
        net_liq += market_value
        positions.append(
            BrokerPosition(
                as_of_ts=as_of,
                account_id=DEMO_ACCOUNT_ID,
                conid=_conid(leg.contract_key),
                contract_key=leg.contract_key,
                quantity=leg.quantity,
                avg_cost=leg.row.price * _MULTIPLIER,
                market_price=leg.row.price,
                market_value=market_value,
                currency=_CURRENCY,
            )
        )
        fills.append(
            BrokerFill(
                account_id=DEMO_ACCOUNT_ID,
                execution_id=f"demo-exec-{as_of.date().isoformat()}-{idx}",
                conid=_conid(leg.contract_key),
                contract_key=leg.contract_key,
                side="BUY" if leg.quantity >= 0 else "SELL",
                quantity=abs(leg.quantity),
                price=leg.row.price,
                currency=_CURRENCY,
                venue_ts=as_of,
                trade_date=as_of.date(),
            )
        )
    cash = [
        BrokerCashBalance(
            as_of_ts=as_of,
            account_id=DEMO_ACCOUNT_ID,
            currency=_CURRENCY,
            cash_balance=-net_liq,
            settled_cash=-net_liq,
            net_liquidation=0.0,
        )
    ]
    return positions, cash, fills


def populate_date(
    store: ParquetStore,
    store_root: Path,
    config: PlatformConfig,
    trade_date: date,
    *,
    primary: str = "SX5E",
) -> DemoWriteReport | None:
    """Populate every position/risk/recon table for one trade date.

    Returns ``None`` when the day has no banked analytics (nothing to build a
    book from); otherwise the count report. Idempotent enough for the demo: it
    overwrites the demo portfolio's rows for the date and re-appends the ledger
    only if that date is not already booked.
    """

    analytics_rows = store.read(
        "projected_option_analytics", trade_date=trade_date, underlying=primary
    )
    if not analytics_rows:
        return None

    basket = build_book(
        _cells_by_underlying(analytics_rows), trade_date, basket_id=DEMO_PORTFOLIO_ID
    )
    legs = _resolve_legs(basket, analytics_rows)
    if not legs:
        return None

    as_of = datetime.combine(trade_date, datetime.min.time(), tzinfo=UTC).replace(
        hour=15, minute=30
    )
    lines = [leg.line for leg in legs]
    px_prov = _prov(trade_date, as_of, "demo-pricer-1.0.0")
    risk_prov = _prov(trade_date, as_of, _CODE_VERSION)

    store.write("pricing_results", _pricing_rows(legs, as_of=as_of, prov=px_prov))
    store.write("risk_aggregates", _aggregate_rows(lines, as_of=as_of, prov=risk_prov))
    store.write(
        "scenario_results", _scenario_rows(lines, config, as_of=as_of, prov=risk_prov)
    )

    positions, cash, broker_fills = _broker_rows(legs, as_of=as_of)
    store.write("broker_positions", positions)
    store.write("broker_cash_balances", cash)
    store.write("broker_fills", broker_fills)

    booking_dir = store_root / _BOOKING_DIRNAME
    booking_dir.mkdir(parents=True, exist_ok=True)
    ledger = JsonlFillsLedger(booking_dir / _FILLS_FILENAME)
    already = ledger.read(trade_date=trade_date)
    fills = _fills_for(legs, trade_date=trade_date, as_of=as_of, prov=px_prov)
    if not already:
        ledger.append_many(fills)

    return DemoWriteReport(
        trade_date=trade_date,
        n_fills=len(fills),
        n_pricings=len(legs),
        n_aggregates=1,
        n_scenarios=len(scenario_grid(config.scenario)) * len(lines)
        + len(stress_surface_grid(config.scenario)) * len(lines),
        n_broker_positions=len(positions),
    )


def populate_store(
    store: ParquetStore,
    store_root: Path,
    config: PlatformConfig,
    *,
    trade_dates: Sequence[date] = DEFAULT_TRADE_DATES,
    primary: str = "SX5E",
) -> list[DemoWriteReport]:
    """Populate the demo book across ``trade_dates``; returns per-date reports."""

    reports: list[DemoWriteReport] = []
    for trade_date in trade_dates:
        report = populate_date(store, store_root, config, trade_date, primary=primary)
        if report is not None:
            reports.append(report)
    return reports


def ensure_demo_book(ctx: AppContext) -> list[DemoWriteReport]:
    """Populate the demo book for ``ctx`` if ``DEMO_BOOK=1`` and it is unpopulated.

    Called once at app startup. A no-op when the flag is unset, and a no-op when
    the demo aggregates are already present (so repeated boots do not re-write).
    """

    if not demo_enabled():
        return []
    existing = [
        row
        for row in ctx.store.read("risk_aggregates")
        if row.portfolio_id == DEMO_PORTFOLIO_ID
    ]
    if existing:
        return []
    config = load_platform_config(ctx.configs_dir)
    return populate_store(ctx.store, ctx.store_root, config)
