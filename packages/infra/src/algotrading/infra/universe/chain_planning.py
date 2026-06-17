from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from algotrading.core.config import StrikeSelectionConfig
from algotrading.infra.contracts import InstrumentKey
from algotrading.infra.pricing import from_forward, price_european

from .errors import StrikeSelectionError

_BOTH_RIGHTS: tuple[str, ...] = ("C", "P")


@dataclass(frozen=True, slots=True)
class ChainSelection:

    max_expiries: int | None = 8
    strike_window_pct: float = 0.35
    min_strikes_per_side: int = 10
    option_exchange: str = "SMART"
    max_strikes_per_session: int | None = None
    tenor_years: tuple[float, ...] = ()
    as_of: date | None = None

    @property
    def targets_tenors(self) -> bool:
        return bool(self.tenor_years) and self.as_of is not None

    def __post_init__(self) -> None:
        if self.max_expiries is not None and self.max_expiries < 1:
            raise ValueError(f"max_expiries must be >= 1 or None, got {self.max_expiries}")
        for tenor in self.tenor_years:
            if not (math.isfinite(tenor) and tenor > 0.0):
                raise ValueError(
                    f"tenor_years must all be finite and > 0, got {tenor!r}"
                )
        if not 0.0 < self.strike_window_pct <= 1.0:
            raise ValueError(
                f"strike_window_pct must be in (0, 1], got {self.strike_window_pct}"
            )
        if self.min_strikes_per_side < 1:
            raise ValueError(
                f"min_strikes_per_side must be >= 1, got {self.min_strikes_per_side}"
            )
        if self.max_strikes_per_session is not None and self.max_strikes_per_session < 1:
            raise ValueError(
                f"max_strikes_per_session must be >= 1 or None, "
                f"got {self.max_strikes_per_session}"
            )


@dataclass(frozen=True, slots=True)
class AvailableChain:

    exchange: str
    trading_class: str
    multiplier: str
    expirations: tuple[str, ...]
    strikes: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class TenorMarket:

    forward: float
    maturity_years: float
    volatility: float
    discount_factor: float


@dataclass(frozen=True, slots=True)
class DeltaBandMarket:

    selection: StrikeSelectionConfig
    markets: Mapping[str, TenorMarket]


@dataclass(frozen=True, slots=True)
class ChainPlan:

    underlying: str
    exchange: str
    trading_class: str
    multiplier: str
    expiries: tuple[str, ...]
    strikes: tuple[float, ...]
    rights: tuple[str, ...]
    available_expiry_count: int
    available_strike_count: int
    spot: float | None

    @property
    def contract_count(self) -> int:
        return len(self.expiries) * len(self.strikes) * len(self.rights)


def select_chain(
    available: Sequence[AvailableChain], symbol: str, option_exchange: str
) -> AvailableChain | None:
    if not available:
        return None
    primary = [chain for chain in available if chain.trading_class == symbol]
    for chain in primary:
        if chain.exchange == option_exchange:
            return chain
    if primary:
        return primary[0]
    for chain in available:
        if chain.exchange == option_exchange:
            return chain
    return available[0]


def select_expiries(
    expirations: Iterable[str], max_expiries: int | None
) -> tuple[str, ...]:
    unique = sorted({expiry for expiry in expirations if expiry})
    if max_expiries is None:
        return tuple(unique)
    return tuple(unique[:max_expiries])


def _parse_expiry_token(token: str) -> date | None:
    if len(token) != 8 or not token.isdigit():
        return None
    try:
        return date(int(token[0:4]), int(token[4:6]), int(token[6:8]))
    except ValueError:
        return None


def tenor_target_dates(as_of: date, tenor_years: Iterable[float]) -> tuple[date, ...]:
    targets: set[date] = set()
    for tenor in tenor_years:
        value = float(tenor)
        if math.isfinite(value) and value > 0.0:
            targets.add(as_of + timedelta(days=round(value * 365.0)))
    return tuple(sorted(targets))


def bracket_dates(listed: Iterable[date], targets: Iterable[date]) -> tuple[date, ...]:
    ordered = sorted(set(listed))
    kept: set[date] = set()
    for target in targets:
        below = [value for value in ordered if value <= target]
        above = [value for value in ordered if value >= target]
        if below:
            kept.add(below[-1])
        if above:
            kept.add(above[0])
    return tuple(sorted(kept))


def select_expiries_bracketing(
    expirations: Iterable[str], *, as_of: date, tenor_years: Iterable[float]
) -> tuple[str, ...]:
    by_date: dict[date, str] = {}
    for token in expirations:
        if not token:
            continue
        parsed = _parse_expiry_token(token)
        if parsed is not None:
            by_date.setdefault(parsed, token)
    kept = bracket_dates(by_date.keys(), tenor_target_dates(as_of, tenor_years))
    return tuple(by_date[value] for value in kept)


def select_strikes(
    strikes: Iterable[float], spot: float | None, selection: ChainSelection
) -> tuple[float, ...]:
    positive = sorted({float(strike) for strike in strikes if float(strike) > 0.0})
    if not positive:
        return ()
    if spot is None or not math.isfinite(spot) or spot <= 0.0:
        mid = len(positive) // 2
        lo = max(0, mid - selection.min_strikes_per_side)
        hi = mid + selection.min_strikes_per_side
        return tuple(positive[lo:hi])

    low = spot * (1.0 - selection.strike_window_pct)
    high = spot * (1.0 + selection.strike_window_pct)
    below = [strike for strike in positive if strike <= spot]
    above = [strike for strike in positive if strike > spot]

    windowed_below = [strike for strike in below if strike >= low]
    if len(windowed_below) < selection.min_strikes_per_side:
        windowed_below = below[-selection.min_strikes_per_side :]
    windowed_above = [strike for strike in above if strike <= high]
    if len(windowed_above) < selection.min_strikes_per_side:
        windowed_above = above[: selection.min_strikes_per_side]

    return tuple(sorted(set(windowed_below) | set(windowed_above)))


def _call_nd1_undiscounted(*, forward: float, strike: float, maturity_years: float,
                           volatility: float, discount_factor: float) -> float:
    state = from_forward(
        forward=forward,
        strike=strike,
        maturity_years=maturity_years,
        volatility=volatility,
        discount_factor=discount_factor,
        option_right="C",
        spot=None,
    )
    return price_european(state).delta / discount_factor


def select_strikes_delta_band(
    strikes: Iterable[float],
    *,
    forward: float,
    maturity_years: float,
    discount_factor: float,
    volatility: float,
    selection: StrikeSelectionConfig,
) -> tuple[float, ...]:
    if not (isinstance(forward, (int, float)) and math.isfinite(forward) and forward > 0.0):
        raise StrikeSelectionError("forward", forward, "must be a finite number > 0")
    if not (
        isinstance(volatility, (int, float)) and math.isfinite(volatility) and volatility > 0.0
    ):
        raise StrikeSelectionError("volatility", volatility, "must be a finite number > 0")
    if not (
        isinstance(maturity_years, (int, float))
        and math.isfinite(maturity_years)
        and maturity_years > 0.0
    ):
        raise StrikeSelectionError(
            "maturity_years", maturity_years, "must be a finite number > 0"
        )
    if not (
        isinstance(discount_factor, (int, float))
        and math.isfinite(discount_factor)
        and 0.0 < discount_factor <= 1.0
    ):
        raise StrikeSelectionError(
            "discount_factor", discount_factor, "must lie in the interval (0, 1]"
        )

    positive = sorted({float(strike) for strike in strikes if float(strike) > 0.0})
    if not positive:
        return ()

    bound = selection.delta_bound
    discounted = selection.delta_convention == "spot_discounted"
    factor = discount_factor if discounted else 1.0

    kept_below: list[float] = []
    kept_above: list[float] = []
    for strike in positive:
        nd1 = _call_nd1_undiscounted(
            forward=forward,
            strike=strike,
            maturity_years=maturity_years,
            volatility=volatility,
            discount_factor=discount_factor,
        )
        call_delta = factor * nd1
        put_delta = factor * (1.0 - nd1)
        if call_delta >= bound and put_delta >= bound:
            (kept_below if strike <= forward else kept_above).append(strike)

    min_per_side = selection.min_strikes_per_side
    below_all = [strike for strike in positive if strike <= forward]
    above_all = [strike for strike in positive if strike > forward]
    if len(kept_below) < min_per_side:
        kept_below = below_all[-min_per_side:]
    if len(kept_above) < min_per_side:
        kept_above = above_all[:min_per_side]

    return tuple(sorted(set(kept_below) | set(kept_above)))


_DISCOVERY_DELTA_MARGIN = 0.10


def discovery_delta_bound(
    economic_bound: float, *, margin: float = _DISCOVERY_DELTA_MARGIN
) -> float:
    floor = 1e-3
    widened = economic_bound - margin
    return max(floor, min(widened, economic_bound - floor))


def select_discovery_strikes(
    strikes: Iterable[float],
    *,
    forward: float,
    maturity_years: float,
    working_vol: float,
    selection: StrikeSelectionConfig,
) -> tuple[float, ...]:
    discovery_selection = selection.model_copy(
        update={"delta_bound": discovery_delta_bound(selection.delta_bound)}
    )
    return select_strikes_delta_band(
        strikes,
        forward=forward,
        maturity_years=maturity_years,
        discount_factor=1.0,
        volatility=working_vol,
        selection=discovery_selection,
    )


def _plan_strikes(
    *,
    chosen: AvailableChain,
    expiries: Sequence[str],
    spot: float | None,
    selection: ChainSelection,
    band: DeltaBandMarket | None,
) -> tuple[float, ...]:
    if band is None or not band.markets:
        return select_strikes(chosen.strikes, spot, selection)

    kept: set[float] = set()
    for expiry in expiries:
        market = band.markets.get(expiry)
        if market is None or not (math.isfinite(market.forward) and market.forward > 0.0):
            kept.update(select_strikes(chosen.strikes, spot, selection))
            continue
        kept.update(
            select_strikes_delta_band(
                chosen.strikes,
                forward=market.forward,
                maturity_years=market.maturity_years,
                discount_factor=market.discount_factor,
                volatility=market.volatility,
                selection=band.selection,
            )
        )
    return tuple(sorted(kept))


def plan_chain(
    underlying: str,
    available: Sequence[AvailableChain],
    *,
    spot: float | None,
    selection: ChainSelection,
    band: DeltaBandMarket | None = None,
) -> ChainPlan | None:
    chosen = select_chain(available, underlying, selection.option_exchange)
    if chosen is None:
        return None
    if selection.tenor_years and selection.as_of is not None:
        expiries = select_expiries_bracketing(
            chosen.expirations, as_of=selection.as_of, tenor_years=selection.tenor_years
        )
    else:
        expiries = select_expiries(chosen.expirations, selection.max_expiries)
    return ChainPlan(
        underlying=underlying,
        exchange=chosen.exchange or selection.option_exchange,
        trading_class=chosen.trading_class,
        multiplier=chosen.multiplier,
        expiries=expiries,
        strikes=_plan_strikes(
            chosen=chosen, expiries=expiries, spot=spot, selection=selection, band=band
        ),
        rights=_BOTH_RIGHTS,
        available_expiry_count=len(chosen.expirations),
        available_strike_count=len(chosen.strikes),
        spot=spot,
    )


def _strikes_by_moneyness(strikes: Sequence[float], spot: float | None) -> list[float]:
    ordered = sorted({float(strike) for strike in strikes})
    if not ordered:
        return []
    if spot is not None and math.isfinite(spot) and spot > 0.0:
        centre = spot
    else:
        centre = ordered[len(ordered) // 2]
    return sorted(ordered, key=lambda strike: (abs(strike - centre), strike))


def select_capture_keys(
    instruments: Iterable[InstrumentKey],
    *,
    spots: Mapping[str, float],
    selection: ChainSelection,
    exchange: str | None = None,
) -> tuple[str, ...]:
    underlyings: list[InstrumentKey] = []
    options_by_underlying: dict[str, list[InstrumentKey]] = defaultdict(list)
    for key in instruments:
        if not key.is_option():
            underlyings.append(key)
            continue
        if key.strike is None or key.expiry is None:
            continue
        if exchange is not None and key.exchange != exchange:
            continue
        options_by_underlying[key.underlying_symbol].append(key)

    selected: list[InstrumentKey] = []
    for symbol, group in options_by_underlying.items():
        if selection.tenor_years and selection.as_of is not None:
            listed = [key.expiry for key in group if key.expiry is not None]
            expiries = list(
                bracket_dates(listed, tenor_target_dates(selection.as_of, selection.tenor_years))
            )
        else:
            expiries = _nearest_expiries(group, selection.max_expiries)
        budget = selection.max_strikes_per_session
        per_expiry = None if budget is None else max(1, budget // max(1, len(expiries)))
        spot = spots.get(symbol)
        for expiry in expiries:
            in_expiry = [key for key in group if key.expiry == expiry]
            if per_expiry is None:
                selected.extend(in_expiry)
                continue
            strikes = [key.strike for key in in_expiry if key.strike is not None]
            keep = set(_strikes_by_moneyness(strikes, spot)[:per_expiry])
            selected.extend(
                key for key in in_expiry if key.strike is not None and float(key.strike) in keep
            )

    underlying_keys = sorted(key.canonical() for key in underlyings)
    option_keys = sorted(key.canonical() for key in selected)
    return (*underlying_keys, *option_keys)


def _nearest_expiries(
    options: Sequence[InstrumentKey], max_expiries: int | None
) -> list[date]:
    expiries = sorted({key.expiry for key in options if key.expiry is not None})
    if max_expiries is None:
        return expiries
    return expiries[:max_expiries]
