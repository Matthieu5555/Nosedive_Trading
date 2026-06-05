"""The single chain-selection policy — broker-agnostic, in two stages.

"Build a surface for any underlying" cannot mean "subscribe to every listed expiry and
strike": a full OCC chain is thousands of contracts, hits broker pacing, and makes an
unusably sparse surface. The chain is narrowed in exactly two places, and both live here
so there is *one* policy, not one per script or per broker:

1. **Discovery** (:func:`plan_chain`) bounds what to *qualify into the universe* from a
   broker's raw chain menu — which listing to expand, which expiries, which strike
   window. It reads the plain :class:`AvailableChain` shape; a broker adapter only
   translates its native chain-discovery response into those rows.
2. **Capture** (:func:`select_capture_keys`) bounds what to *actually stream* from the
   already-resolved universe — the nearest-the-money strikes across the nearest
   maturities, capped to a broker's per-session strike budget. It reads
   :class:`~contracts.InstrumentKey` and returns the canonical keys to subscribe to.

Both stages share one :class:`ChainSelection` config, so the bound on maturities and the
ATM emphasis are defined once. Everything is unit-tested with plain stand-ins, no broker
present.

This is where the SPY/2SPY lesson lives generally: a name lists several trading classes
(the primary ``SPY`` plus secondary settlement classes ``2SPY``/``3SPY`` whose strike
and expiry grids do *not* combine into the same listed contracts), so :func:`select_chain`
prefers the primary class before falling back, rather than expanding the wrong listing's
cartesian product into phantom contracts.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from contracts import InstrumentKey

# Both option rights are always planned; the adapter qualifies calls and puts at each
# (expiry, strike). Kept as a named constant so the one place that fixes the pair is here.
_BOTH_RIGHTS: tuple[str, ...] = ("C", "P")


@dataclass(frozen=True, slots=True)
class ChainSelection:
    """How much of an underlying's option chain to qualify into the universe.

    The defaults bound the request to a band of maturities and a strike window around
    spot that is dense enough to fit a slice yet small enough to qualify quickly.

    - ``max_expiries`` — keep the nearest N expirations (chronological). This is the
      maturity budget for *both* stages: discovery qualifies these, capture streams them.
    - ``strike_window_pct`` — keep strikes within ±this fraction of spot (e.g. ``0.35``
      is spot ±35%). Used by discovery (:func:`select_strikes`).
    - ``min_strikes_per_side`` — but always keep at least this many strikes below and
      above spot, even if they fall outside the window, so a low-volatility name with a
      wide strike ladder still yields enough points for a fit. Used by discovery.
    - ``option_exchange`` — which listing to prefer when a name lists on several
      exchanges (``SMART`` is the aggregated smart-routed chain).
    - ``max_strikes_per_session`` — the *capture* budget: the broker's cap on how many
      strikes (a strike = one call + one put) to stream per session, split across the
      kept maturities by :func:`select_capture_keys`. ``None`` means uncapped — stream
      every resolved contract in the kept maturities.
    """

    max_expiries: int = 8
    strike_window_pct: float = 0.35
    min_strikes_per_side: int = 10
    option_exchange: str = "SMART"
    max_strikes_per_session: int | None = None

    def __post_init__(self) -> None:
        if self.max_expiries < 1:
            raise ValueError(f"max_expiries must be >= 1, got {self.max_expiries}")
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
    """One listing a broker offered for an underlying: a menu of expiries and strikes.

    The broker-neutral normalization of a single chain-discovery row. A name lists one
    of these per (exchange, trading class); the planner picks among them. ``trading_class``
    distinguishes the primary listing (equal to the underlying symbol) from secondary
    settlement classes whose grids do not combine with it.
    """

    exchange: str
    trading_class: str
    multiplier: str
    expirations: tuple[str, ...]
    strikes: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ChainPlan:
    """The bounded request the planner chose: which contracts to qualify, and why.

    ``expiries`` × ``strikes`` × ``rights`` is the cartesian a broker adapter expands
    and qualifies (not every combination trades — the adapter drops the ones that fail
    to resolve). The ``available_*`` counts and ``spot`` are diagnostics: they record
    how wide the offered chain was and what the window was centered on, so a thin plan
    is explainable without re-running discovery.
    """

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
        """How many (expiry, strike, right) contracts this plan asks to qualify."""
        return len(self.expiries) * len(self.strikes) * len(self.rights)


def select_chain(
    available: Sequence[AvailableChain], symbol: str, option_exchange: str
) -> AvailableChain | None:
    """Pick the one listing to expand into contracts.

    A name like SPY lists several trading classes — the primary ``SPY`` plus secondary
    settlement classes (``2SPY``, ``3SPY``, …) whose strike and expiry *grids do not
    combine into the same listed contracts*. Expanding the wrong listing's cartesian
    product yields phantom options that fail to qualify, so the order of preference is:

    1. the primary class on the requested exchange (``trading_class == symbol`` and
       ``exchange == option_exchange``) — the standard monthly+weekly SPY listing;
    2. the primary class on any exchange;
    3. any class on the requested exchange (a name whose primary class is not ``symbol``);
    4. the first listing offered.

    ``None`` when nothing was offered (an unknown or option-less symbol), which the
    caller turns into a stock-only universe rather than an error.
    """
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


def select_expiries(expirations: Iterable[str], max_expiries: int) -> tuple[str, ...]:
    """Keep the nearest ``max_expiries`` expirations.

    Expirations are ``YYYYMMDD`` strings, which sort chronologically as text, so the
    nearest maturities are the first after a lexical sort of the de-duplicated set.
    """
    unique = sorted({expiry for expiry in expirations if expiry})
    return tuple(unique[:max_expiries])


def select_strikes(
    strikes: Iterable[float], spot: float | None, selection: ChainSelection
) -> tuple[float, ...]:
    """Keep the strikes within the configured window around spot, in ascending order.

    With a usable ``spot``: keep strikes inside ``spot ± strike_window_pct``, but always
    keep at least ``min_strikes_per_side`` strikes immediately below and above spot, so a
    name whose strike ladder is wider than the window still yields enough points to fit.
    Without a spot (no snapshot available): fall back to a symmetric block of
    ``min_strikes_per_side`` strikes either side of the median listed strike — bounded and
    deterministic, just not centered on the true forward.
    """
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


def plan_chain(
    underlying: str,
    available: Sequence[AvailableChain],
    *,
    spot: float | None,
    selection: ChainSelection,
) -> ChainPlan | None:
    """Compose a bounded :class:`ChainPlan` from the listings a broker offered.

    Picks the listing (:func:`select_chain`), the nearest expiries (:func:`select_expiries`),
    and the strike window (:func:`select_strikes`), then records the offered counts and the
    centering spot as diagnostics. ``None`` when no listing was offered — the caller builds
    a stock-only universe rather than raising. The chosen listing's exchange falls back to
    the requested ``option_exchange`` when the broker left it blank, so the plan always
    names a concrete exchange to qualify against.
    """
    chosen = select_chain(available, underlying, selection.option_exchange)
    if chosen is None:
        return None
    return ChainPlan(
        underlying=underlying,
        exchange=chosen.exchange or selection.option_exchange,
        trading_class=chosen.trading_class,
        multiplier=chosen.multiplier,
        expiries=select_expiries(chosen.expirations, selection.max_expiries),
        strikes=select_strikes(chosen.strikes, spot, selection),
        rights=_BOTH_RIGHTS,
        available_expiry_count=len(chosen.expirations),
        available_strike_count=len(chosen.strikes),
        spot=spot,
    )


def _strikes_by_moneyness(strikes: Sequence[float], spot: float | None) -> list[float]:
    """Order strikes nearest-the-money first, with a deterministic tie-break.

    With a usable ``spot`` the centre is the spot, so the nearest-the-money strikes come
    first — they are the most liquid and the ones the forward/IV/surface stack actually
    needs. Without a spot (no snapshot for the underlying) the centre falls back to the
    median listed strike, which keeps the order bounded and deterministic, just not
    centred on the true forward. Ties (two strikes equidistant from the centre) break on
    the strike itself, so the order never depends on input ordering or set iteration.
    """
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
    """Pick the canonical keys to subscribe to from a resolved universe — capture stage.

    :func:`plan_chain` bounds *discovery* (which contracts to qualify into the universe,
    from a broker's raw chain menu). This bounds *capture*: which of those resolved
    contracts to actually stream. A full bounded universe is still hundreds of contracts
    and the broker enforces request pacing, so subscribing to everything blindly is
    unsafe — moneyness is the cheap pre-subscription proxy for liquidity (open interest
    and spread are only observable post-subscription, and QC filters on them downstream).

    Underlyings are always kept (the collector needs each underlying's spot to centre its
    options) and are not subject to the ``exchange`` filter. Options are optionally
    restricted to ``exchange``, grouped by underlying, bounded to the nearest
    ``selection.max_expiries`` maturities, then — when ``selection.max_strikes_per_session``
    is set — capped to that strike budget split evenly across the kept maturities, keeping
    the nearest-the-money strikes (both rights) per maturity. The budget is counted in
    *strikes* (a strike = one call + one put), matching the broker's own limit. With the
    budget ``None`` every contract in the kept maturities is streamed.

    Returns canonical instrument-key strings: the underlyings (sorted) first, then the
    selected option keys (sorted) — each group ordered independently and deterministically,
    so the same universe and spots always yield the same subscription set in the same order.
    """
    underlyings: list[InstrumentKey] = []
    options_by_underlying: dict[str, list[InstrumentKey]] = defaultdict(list)
    for key in instruments:
        if not key.is_option():
            underlyings.append(key)
            continue
        if key.strike is None or key.expiry is None:
            continue  # a malformed option key cannot be ranked; the resolver rejects these
        if exchange is not None and key.exchange != exchange:
            continue
        options_by_underlying[key.underlying_symbol].append(key)

    selected: list[InstrumentKey] = []
    for symbol, group in options_by_underlying.items():
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


def _nearest_expiries(options: Sequence[InstrumentKey], max_expiries: int) -> list[date]:
    """The nearest ``max_expiries`` distinct option expiries, chronological."""
    expiries = sorted({key.expiry for key in options if key.expiry is not None})
    return expiries[:max_expiries]
