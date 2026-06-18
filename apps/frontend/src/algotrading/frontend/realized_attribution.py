"""Realized, day-over-day Greek P&L explain anchored to a *fixed* option expiry.

The banked ``projected_option_analytics`` grid is constant-maturity: every "3m" cell is
exactly T=0.25 on every banked day. Tracking such a cell close-to-close gives ``d_time``
= 0, so theta vanishes and the realized residual blows up (the move that *should* be
explained by one day of time decay lands entirely in the residual instead).

To make theta real we anchor each leg to a FIXED calendar expiry (e.g. the September
SX5E expiry) resolved from the raw ``iv_points`` option chain, and roll its maturity down
by the actual calendar days elapsed between the two close dates (~1/365 yr per day). The
strike is held fixed (a real contract does not re-strike); its implied vol and the implied
forward are re-read from the chain on each date. ``RealizedMove.between`` then sees a true
``d_time`` and the Taylor decomposition reproduces the close-to-close reprice to a small,
honest residual.

This module owns only the *resolution* (chain -> ``ContractValuationInput`` per leg per
date) and the per-day-step orchestration. The decomposition itself is the already-tested
``attribute_realized_book`` in ``algotrading.infra.risk.attribution``; we do not reimplement
the Taylor kernel here.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from algotrading.infra.risk import ContractValuationInput, PositionRisk, position_risk
from algotrading.infra.risk.attribution import RealizedBookAttribution, attribute_realized_book
from algotrading.infra.risk.config import AttributionConfig
from algotrading.infra.storage import ParquetStore

# A fixed expiry rolls its maturity down by the actual calendar days elapsed. The pricer's
# year fraction is ACT/365 (matches the engine's theta_day_count default of 365), so one
# calendar day is exactly 1/365 yr of decay — the whole point of anchoring to a real expiry.
_DAYS_PER_YEAR = 365.0

_IV_POINTS_TABLE = "iv_points"
_DEMO_PORTFOLIO_ID = "demo-sep-straddle"
_DEMO_EXPIRY = date(2026, 9, 18)  # September SX5E standard expiry (the showpiece)
_DEFAULT_UNDERLYING = "SX5E"
_DEFAULT_MULTIPLIER = 100.0
_DEFAULT_CURRENCY = "EUR"


class RealizedAttributionInputError(Exception):
    """A realized-attribution request cannot be served from banked data."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class StraddleLeg:
    """One leg of the fixed-expiry book: a real contract, held by strike and right."""

    strike: float
    option_right: str  # "C" or "P"
    quantity: float


@dataclass(frozen=True, slots=True)
class BookSpec:
    """A fixed-expiry option book to attribute, resolved from the raw chain each date."""

    portfolio_id: str
    underlying: str
    expiry: date
    multiplier: float
    currency: str
    legs: tuple[StraddleLeg, ...]


@dataclass(frozen=True, slots=True)
class _ChainQuote:
    implied_vol: float
    forward: float


def september_straddle_spec(
    *,
    underlying: str = _DEFAULT_UNDERLYING,
    expiry: date = _DEMO_EXPIRY,
    multiplier: float = _DEFAULT_MULTIPLIER,
    currency: str = _DEFAULT_CURRENCY,
    portfolio_id: str = _DEMO_PORTFOLIO_ID,
    strike: float | None = None,
) -> BookSpec:
    """The demo showpiece: a long ATM straddle on the fixed September expiry.

    Strike is left unresolved here (``None``); the caller resolves the ATM strike from the
    first date's chain so the *same* fixed strike is tracked across every day-step. When a
    strike is supplied (e.g. from a re-bind), both legs are pinned to it.
    """
    if strike is None:
        # Sentinel: legs get their strike filled in by resolve_book once date-0 is known.
        legs: tuple[StraddleLeg, ...] = ()
    else:
        legs = (
            StraddleLeg(strike=strike, option_right="C", quantity=1.0),
            StraddleLeg(strike=strike, option_right="P", quantity=1.0),
        )
    return BookSpec(
        portfolio_id=portfolio_id,
        underlying=underlying,
        expiry=expiry,
        multiplier=multiplier,
        currency=currency,
        legs=legs,
    )


def _expiry_label(expiry: date) -> str:
    return expiry.isoformat()


def _read_chain(
    store: ParquetStore, *, underlying: str, trade_date: date, expiry: date
) -> dict[tuple[float, str], _ChainQuote]:
    """Read the raw option chain for one fixed expiry on one date.

    The ``iv_points`` ``contract_key`` is
    ``SX5E|OPT|EUREX|EUR|<mult>|<conid>|<expiry>|<K>|<right>``.
    The chain stores ``implied_vol`` and ``log_moneyness`` but not the forward directly; the
    forward is recovered as ``K / exp(log_moneyness)`` (consistent across strikes for a fixed
    expiry, which is how the surface was fit).
    """
    label = _expiry_label(expiry)
    rows = store.read(_IV_POINTS_TABLE, trade_date=trade_date, underlying=underlying)
    chain: dict[tuple[float, str], _ChainQuote] = {}
    for row in rows:
        parts = row.contract_key.split("|")
        if len(parts) < 9 or parts[6] != label:
            continue
        try:
            strike = float(parts[7])
        except ValueError:
            continue
        right = parts[8]
        forward = strike / math.exp(row.log_moneyness)
        chain[(strike, right)] = _ChainQuote(implied_vol=row.implied_vol, forward=forward)
    return chain


def _forward_of(chain: dict[tuple[float, str], _ChainQuote]) -> float:
    return next(iter(chain.values())).forward


def _atm_strike(chain: dict[tuple[float, str], _ChainQuote]) -> float:
    """The listed strike closest to the implied forward, present as BOTH a call and a put."""
    forward = _forward_of(chain)
    both_sides = {strike for (strike, _right) in chain} & {
        strike for (strike, right) in chain if (strike, "C") in chain and (strike, "P") in chain
    }
    candidates = sorted(both_sides)
    if not candidates:
        raise RealizedAttributionInputError(
            "no_atm_strike", "no strike carries both a call and a put on the start date"
        )
    return min(candidates, key=lambda strike: abs(strike - forward))


def _maturity_years(trade_date: date, expiry: date) -> float:
    return (expiry - trade_date).days / _DAYS_PER_YEAR


def _leg_contract_key(spec: BookSpec, leg: StraddleLeg) -> str:
    return f"{spec.underlying}|{_expiry_label(spec.expiry)}|{int(leg.strike)}|{leg.option_right}"


def _leg_valuation(
    spec: BookSpec,
    leg: StraddleLeg,
    *,
    trade_date: date,
    chain: dict[tuple[float, str], _ChainQuote],
) -> ContractValuationInput:
    quote = chain.get((leg.strike, leg.option_right))
    if quote is None:
        raise RealizedAttributionInputError(
            "leg_not_in_chain",
            f"{leg.option_right} strike {leg.strike:g} of {spec.underlying} {spec.expiry} "
            f"is absent from the {trade_date.isoformat()} chain",
        )
    return ContractValuationInput(
        contract_key=_leg_contract_key(spec, leg),
        underlying=spec.underlying,
        option_right=leg.option_right,
        exercise_style="european",
        strike=leg.strike,
        maturity_years=_maturity_years(trade_date, spec.expiry),
        spot=quote.forward,
        carry=0.0,
        volatility=quote.implied_vol,
        discount_factor=1.0,
        multiplier=spec.multiplier,
        currency=spec.currency,
    )


def resolve_book(
    store: ParquetStore, spec: BookSpec, *, start_date: date
) -> BookSpec:
    """Pin a strike-less demo spec to the ATM strike read from the start date's chain."""
    if spec.legs:
        return spec
    chain = _read_chain(
        store, underlying=spec.underlying, trade_date=start_date, expiry=spec.expiry
    )
    if not chain:
        raise RealizedAttributionInputError(
            "no_chain",
            f"no {spec.underlying} {spec.expiry.isoformat()} option chain banked for "
            f"{start_date.isoformat()}",
        )
    strike = _atm_strike(chain)
    legs = (
        StraddleLeg(strike=strike, option_right="C", quantity=1.0),
        StraddleLeg(strike=strike, option_right="P", quantity=1.0),
    )
    return BookSpec(
        portfolio_id=spec.portfolio_id,
        underlying=spec.underlying,
        expiry=spec.expiry,
        multiplier=spec.multiplier,
        currency=spec.currency,
        legs=legs,
    )


def _start_lines(
    store: ParquetStore, spec: BookSpec, *, trade_date: date
) -> list[PositionRisk]:
    chain = _read_chain(
        store, underlying=spec.underlying, trade_date=trade_date, expiry=spec.expiry
    )
    if not chain:
        raise RealizedAttributionInputError(
            "no_chain",
            f"no {spec.underlying} {spec.expiry.isoformat()} option chain banked for "
            f"{trade_date.isoformat()}",
        )
    return [
        position_risk(
            portfolio_id=spec.portfolio_id,
            quantity=leg.quantity,
            valuation=_leg_valuation(spec, leg, trade_date=trade_date, chain=chain),
        )
        for leg in spec.legs
    ]


def _end_states(
    store: ParquetStore, spec: BookSpec, *, trade_date: date
) -> dict[str, ContractValuationInput]:
    chain = _read_chain(
        store, underlying=spec.underlying, trade_date=trade_date, expiry=spec.expiry
    )
    if not chain:
        raise RealizedAttributionInputError(
            "no_chain",
            f"no {spec.underlying} {spec.expiry.isoformat()} option chain banked for "
            f"{trade_date.isoformat()}",
        )
    states: dict[str, ContractValuationInput] = {}
    for leg in spec.legs:
        valuation = _leg_valuation(spec, leg, trade_date=trade_date, chain=chain)
        states[valuation.contract_key] = valuation
    return states


@dataclass(frozen=True, slots=True)
class RealizedDayStep:
    """One consecutive close-to-close step of the realized attribution waterfall."""

    start_date: date
    end_date: date
    attribution: RealizedBookAttribution


def attribute_day_steps(
    store: ParquetStore,
    spec: BookSpec,
    dates: Sequence[date],
    config: AttributionConfig,
) -> list[RealizedDayStep]:
    """Decompose each consecutive (date[i] -> date[i+1]) close move of the fixed book.

    The book's strike is resolved once, from ``dates[0]``, then held fixed across every step
    (a real contract does not re-strike day to day). Each step re-reads vol and the implied
    forward for that fixed strike on both its dates and rolls the maturity down by the actual
    calendar days elapsed, so ``d_time`` is real and theta does not vanish.
    """
    if len(dates) < 2:
        raise RealizedAttributionInputError(
            "too_few_dates", "need at least two trade dates to form a day-step"
        )
    resolved = resolve_book(store, spec, start_date=dates[0])
    steps: list[RealizedDayStep] = []
    for start_date, end_date in zip(dates[:-1], dates[1:], strict=True):
        starts = _start_lines(store, resolved, trade_date=start_date)
        ends = _end_states(store, resolved, trade_date=end_date)
        attribution = attribute_realized_book(starts, ends, config)
        steps.append(
            RealizedDayStep(
                start_date=start_date, end_date=end_date, attribution=attribution
            )
        )
    return steps
