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

Strike selection itself comes in two coexisting policies over the *same* listed-strike
shape — one policy surface, not one per broker or per script (WS 1B):

* **%-of-spot** (:func:`select_strikes`) — keep strikes inside ``spot ± strike_window_pct``
  with a per-side floor. A request-shaping heuristic; its window lives in code.
* **delta band** (:func:`select_strikes_delta_band`) — keep, **per tenor**, the contiguous
  block of listed strikes from the 30Δ put through ATM to the 30Δ call. The 30Δ bound and
  the delta convention are economic and come from typed ``universe.yaml`` config
  (:class:`~algotrading.core.config.StrikeSelectionConfig`), never a ``.py`` literal; delta
  is read from the pricing engine at ``carry == 0`` (so spot and forward delta coincide),
  never re-derived here.

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
from datetime import date, timedelta

from algotrading.core.config import StrikeSelectionConfig
from algotrading.infra.contracts import InstrumentKey
from algotrading.infra.pricing import from_forward, price_european

from .errors import StrikeSelectionError

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
    - ``tenor_years`` / ``as_of`` — when both are set, expiry selection switches from the
      legacy "nearest ``max_expiries``" to the **tenor-targeted bracket**
      (:func:`select_expiries_bracketing`): for each pinned tenor (a year fraction in
      ``tenor_years``) the listed expiries straddling ``as_of + tenor·365`` are kept, so the
      captured chain spans the term structure instead of collapsing onto the front month.
      ``tenor_years`` empty (the default) keeps the legacy nearest-N behaviour, so every
      caller that does not opt in is unchanged. ``max_expiries`` still bounds the legacy path
      and remains the per-stage maturity budget there.
    """

    max_expiries: int = 8
    strike_window_pct: float = 0.35
    min_strikes_per_side: int = 10
    option_exchange: str = "SMART"
    max_strikes_per_session: int | None = None
    tenor_years: tuple[float, ...] = ()
    as_of: date | None = None

    @property
    def targets_tenors(self) -> bool:
        """Whether expiry selection uses the tenor-targeted bracket (both inputs present)."""
        return bool(self.tenor_years) and self.as_of is not None

    def __post_init__(self) -> None:
        if self.max_expiries < 1:
            raise ValueError(f"max_expiries must be >= 1, got {self.max_expiries}")
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
class TenorMarket:
    """The per-expiry market inputs the delta-band strike selection needs for one maturity.

    The delta band is a *per-tenor* policy: the same dollar strike is a different delta at
    each maturity, so a single representative selection is the silent wrong answer (the 1B
    spec calls this out). Discovery therefore supplies, per expiry it kept, the forward, the
    working ATM vol, and the discount factor as-of the date being planned for — every input
    point-in-time honest, no look-ahead.
    """

    forward: float
    maturity_years: float
    volatility: float
    discount_factor: float


@dataclass(frozen=True, slots=True)
class DeltaBandMarket:
    """The economic inputs that switch :func:`plan_chain` onto the delta-band strike policy.

    Carries the hashed :class:`~algotrading.core.config.StrikeSelectionConfig` (the 30Δ bound
    + convention, never a ``.py`` literal) and the per-expiry market state (:class:`TenorMarket`,
    keyed by the ``YYYYMMDD`` expiry string the menu lists). When this is supplied to
    :func:`plan_chain`, each kept expiry's strike block is selected by
    :func:`select_strikes_delta_band` against *that expiry's* forward/vol/discount; an expiry
    with no market entry, or no usable forward, falls back to the %-of-spot window so discovery
    still yields a plan (the discovery fallback the spec preserves). With ``markets`` empty the
    whole plan falls back to %-of-spot.
    """

    selection: StrikeSelectionConfig
    markets: Mapping[str, TenorMarket]


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


def _parse_expiry_token(token: str) -> date | None:
    """A ``YYYYMMDD`` expiry string → a :class:`date`, or ``None`` if it does not parse."""
    if len(token) != 8 or not token.isdigit():
        return None
    try:
        return date(int(token[0:4]), int(token[4:6]), int(token[6:8]))
    except ValueError:
        return None


def tenor_target_dates(as_of: date, tenor_years: Iterable[float]) -> tuple[date, ...]:
    """The calendar date each pinned tenor points at: ``as_of + tenor·365`` (ACT/365).

    De-duplicated and chronological. Non-finite or non-positive tenors are skipped (a
    defensive floor — :class:`ChainSelection` already rejects them). ACT/365 matches the
    pinned-tenor year-fraction map the projection uses (``surfaces.projection.tenor_years``),
    so a capture target and its projection tenor land on the same convention.
    """
    targets: set[date] = set()
    for tenor in tenor_years:
        value = float(tenor)
        if math.isfinite(value) and value > 0.0:
            targets.add(as_of + timedelta(days=round(value * 365.0)))
    return tuple(sorted(targets))


def bracket_dates(listed: Iterable[date], targets: Iterable[date]) -> tuple[date, ...]:
    """The listed dates straddling each target: nearest at-or-below and nearest at-or-above.

    For each target the nearest listed date ``<=`` it and the nearest ``>=`` it are kept, so
    a value interpolated at the target sits between two real observations rather than off the
    end of them. A target that falls exactly on a listed date selects that one date (both
    bounds coincide). A target past the end of the listing keeps only the side that exists —
    the one-sided long-end case, surfaced downstream as a coverage gap, never back-filled.
    Returns the union over all targets, de-duplicated and chronological. Deterministic and
    wall-clock-free.
    """
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
    """Keep, per pinned tenor, the listed expiries straddling that tenor's target date.

    The tenor-targeted replacement for :func:`select_expiries`'s "nearest N". The target for a
    tenor is ``as_of + tenor·365`` (:func:`tenor_target_dates`); the kept set is the union over
    all tenors of the bracketing listed expiries (:func:`bracket_dates`). This makes the
    captured chain span the term structure — a point either side of each pinned tenor so the
    surface projection interpolates rather than extrapolates — instead of collapsing onto the
    front month, which is what "nearest N" does when the front month alone lists N weeklies.

    Adjacent short tenors share front-month expiries; the union de-duplicates them. A tenor with
    no listed expiry on one side (the long end of a thin listing) contributes only the side that
    exists. Expirations are ``YYYYMMDD`` strings; unparseable tokens are skipped. The result is
    chronological and de-duplicated, deterministic and wall-clock-free, so the captured set is
    byte-identical on replay (ADR 0027).
    """
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


def _call_nd1_undiscounted(*, forward: float, strike: float, maturity_years: float,
                           volatility: float, discount_factor: float) -> float:
    """The undiscounted forward call delta ``N(d1)`` for one strike, via the pricing engine.

    Built at ``carry == 0`` with :func:`pricing.from_forward` (``spot=None``), so spot and
    forward delta coincide. The engine returns the *discounted* spot delta
    (``discount_factor · N(d1)`` for a call), so dividing by the discount factor recovers
    the undiscounted ``N(d1)`` — the engine is the single source of the delta, never a
    re-derivation here. ``N(d1)`` is the one monotone quantity the band keys off: it falls
    from ~1 (deep ITM call / deep OTM put) through 0.5 (ATM) to ~0 (deep OTM call) as the
    strike rises, so the put and call sides are two thresholds on the same number.
    """
    state = from_forward(
        forward=forward,
        strike=strike,
        maturity_years=maturity_years,
        volatility=volatility,
        discount_factor=discount_factor,
        option_right="C",
        spot=None,  # carry == 0: spot delta == forward delta (the convention pin)
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
    """Keep the contiguous block of listed strikes from the 30Δ put through ATM to the 30Δ call.

    The second strike-selection policy over the same listed-strike shape as
    :func:`select_strikes`, applied **per tenor** (the forward and maturity differ by
    expiry, so the same dollar strike is a different delta at each maturity — selecting once
    on a representative tenor is the silent wrong answer). For each listed strike a call
    state is built at ``carry == 0`` (:func:`pricing.from_forward`, ``spot=None``) so spot
    delta and forward delta coincide, and its delta is read from the pricing engine — never
    re-derived here.

    A strike is kept when **both** its call-delta magnitude and its put-delta magnitude are
    at least ``selection.delta_bound``: that is exactly the central block bounded by the
    30Δ call (where the call delta falls to the bound) and the 30Δ put (where the put delta
    falls to the bound). ATM (both magnitudes ≈ 0.5) is always inside; the wings (one
    magnitude below the bound) are excluded. The comparison is ``>=`` so a strike sitting
    *exactly* on the 30Δ boundary is kept (the boundary-exact case).

    ``selection.delta_convention`` pins which delta the bound is read against, built at the
    same ``carry == 0`` so the two coincide up to the discount factor:
    ``forward_undiscounted`` measures against the forward delta ``N(d1)`` /
    ``1 − N(d1)``; ``spot_discounted`` measures against the engine's discounted spot delta
    ``discount_factor · N(d1)`` / ``discount_factor · (1 − N(d1))``. They differ only by the
    discount factor, which can move the boundary strike — hence the flag is pinned.

    Returns the kept strikes ascending and de-duplicated, exactly as :func:`select_strikes`.
    When fewer than ``selection.min_strikes_per_side`` listed strikes fall inside the band
    on a side (a thin listing, or an all-wing ladder where nothing is inside 30Δ), the
    nearest-the-money block of ``min_strikes_per_side`` strikes either side of the forward is
    returned instead — a labeled floor, never an empty silent result.

    No look-ahead: every input (forward, working vol, discount factor) is as-of the
    snapshot/date being selected for; the function reads no wall clock and no later
    observation.
    """
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

    # Per-side floor — only when the band yielded fewer than the minimum on a side, exactly
    # as the %-of-spot select_strikes does. A thin listing or an all-wing ladder (nothing
    # inside 30Δ) then still returns the nearest-the-money block, a labeled floor (see the
    # docstring), never an empty silent result. A side already at or above the floor is left
    # as the band found it, so a dense central listing is not inflated past the 30Δ window.
    min_per_side = selection.min_strikes_per_side
    below_all = [strike for strike in positive if strike <= forward]
    above_all = [strike for strike in positive if strike > forward]
    if len(kept_below) < min_per_side:
        kept_below = below_all[-min_per_side:]
    if len(kept_above) < min_per_side:
        kept_above = above_all[:min_per_side]

    return tuple(sorted(set(kept_below) | set(kept_above)))


def _plan_strikes(
    *,
    chosen: AvailableChain,
    expiries: Sequence[str],
    spot: float | None,
    selection: ChainSelection,
    band: DeltaBandMarket | None,
) -> tuple[float, ...]:
    """The strike block for a plan — delta-band per expiry when configured, else %-of-spot.

    With no :class:`DeltaBandMarket` (``band is None`` or it carries no market for any kept
    expiry) this is exactly the historical %-of-spot window (:func:`select_strikes`), so the
    discovery fallback is preserved verbatim. With a band, the kept strike set is the *union*
    over the kept expiries of each expiry's :func:`select_strikes_delta_band` block — the
    economic 30Δ selection on the production policy path, one block per tenor (the per-tenor
    discipline the spec requires), collapsed to the single strike axis ``ChainPlan`` qualifies
    (a broker qualifies the cartesian expiries × strikes, so the union is the right superset).
    An expiry the band has no market for falls back to the %-of-spot window for that expiry, so
    a partially-covered menu still yields a plan rather than dropping strikes silently.
    """
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
    """Compose a bounded :class:`ChainPlan` from the listings a broker offered.

    Picks the listing (:func:`select_chain`), the nearest expiries (:func:`select_expiries`),
    and the strike block, then records the offered counts and the centering spot as
    diagnostics. The strike block is the **delta band** (:func:`select_strikes_delta_band`,
    per kept expiry) when a :class:`DeltaBandMarket` is supplied — the economic 30Δ policy the
    production EOD path drives — and falls back to the **%-of-spot** window
    (:func:`select_strikes`) when no band market is available (the discovery fallback). ``None``
    when no listing was offered — the caller builds a stock-only universe rather than raising.
    The chosen listing's exchange falls back to the requested ``option_exchange`` when the
    broker left it blank, so the plan always names a concrete exchange to qualify against.
    """
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


def _nearest_expiries(options: Sequence[InstrumentKey], max_expiries: int) -> list[date]:
    """The nearest ``max_expiries`` distinct option expiries, chronological."""
    expiries = sorted({key.expiry for key in options if key.expiry is not None})
    return expiries[:max_expiries]
