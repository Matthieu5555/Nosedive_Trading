"""Decide which slice of an option chain to qualify — broker-agnostic policy.

"Build a surface for any underlying" cannot mean "qualify every listed expiry and
strike": a full OCC chain is thousands of contracts, hits broker pacing, and makes an
unusably sparse surface. This module owns the *policy* that bounds the request — which
listing to expand, which expiries, which strikes — entirely in broker-neutral terms.

The shape it works over is :class:`AvailableChain`: one normalized listing (a menu of
expirations and strikes for an exchange + trading class). A broker adapter is
responsible only for *translating* its native chain-discovery response into
``AvailableChain`` rows; everything after — pick a listing, keep the nearest maturities,
window the strikes around spot — is here and is unit-tested with plain stand-ins, no
broker present. :func:`plan_chain` composes the three into a :class:`ChainPlan` the
adapter then expands into real contracts to qualify.

This is where the SPY/2SPY lesson lives generally: a name lists several trading classes
(the primary ``SPY`` plus secondary settlement classes ``2SPY``/``3SPY`` whose strike
and expiry grids do *not* combine into the same listed contracts), so :func:`select_chain`
prefers the primary class before falling back, rather than expanding the wrong listing's
cartesian product into phantom contracts.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Both option rights are always planned; the adapter qualifies calls and puts at each
# (expiry, strike). Kept as a named constant so the one place that fixes the pair is here.
_BOTH_RIGHTS: tuple[str, ...] = ("C", "P")


@dataclass(frozen=True, slots=True)
class ChainSelection:
    """How much of an underlying's option chain to qualify into the universe.

    The defaults bound the request to a band of maturities and a strike window around
    spot that is dense enough to fit a slice yet small enough to qualify quickly.

    - ``max_expiries`` — keep the nearest N expirations (chronological).
    - ``strike_window_pct`` — keep strikes within ±this fraction of spot (e.g. ``0.35``
      is spot ±35%).
    - ``min_strikes_per_side`` — but always keep at least this many strikes below and
      above spot, even if they fall outside the window, so a low-volatility name with a
      wide strike ladder still yields enough points for a fit.
    - ``option_exchange`` — which listing to prefer when a name lists on several
      exchanges (``SMART`` is the aggregated smart-routed chain).
    """

    max_expiries: int = 8
    strike_window_pct: float = 0.35
    min_strikes_per_side: int = 10
    option_exchange: str = "SMART"

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
