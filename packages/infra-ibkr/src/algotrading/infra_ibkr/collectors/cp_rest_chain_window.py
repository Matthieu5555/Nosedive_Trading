"""IBKR option-chain discovery-window policy: month tokens + the delta-driven strike window.

Extracted from the close capture (pure code motion): which listed month tokens discovery
qualifies (:func:`select_discovery_months`) and which listed strikes it qualifies per expiry
(:func:`qualify_strikes_for_expiry`) — the T-delta-window policy. The capture orchestration
stays in :mod:`.cp_rest_close_capture`; this module owns the policy and its failure modes.

Discovery strike qualification is delta-driven and tenor-aware (T-delta-window): per expiry we
qualify the listed strikes that *contain* the 30Δ band at that tenor, computed from the index
spot and a conservative working vol via ``select_discovery_strikes``. This REPLACED a fixed
near-the-money strike count (``_DISCOVERY_STRIKES_PER_SIDE = 16``, ±~1%): the count silently
clipped the 30Δ band, whose strike width grows with √T — at 3y the 30Δ call sat ~+18% out while
±16 reached only ±1%, so ``delta_band_completeness`` QC failed and the band was never delivered.
A flat count cannot bound a band whose width scales with maturity, so it is gone, not retuned.

Pacing: conid resolution costs one paced ``/iserver/secdef/info`` call per (strike, right), so a
wider window is more paced calls. Per the owner ruling (2026-06-12) we DO NOT cap the band — a
generous strike cap is the very intent-vs-delivery bound this task removed, just relabelled, and
would re-clip the 30Δ. Instead the window is full-30Δ (a true superset), bounded in practice by
the broker's listed strikes (coarse spacing at the long end → tens of strikes, not hundreds),
with a fail-LOUD runaway guard far above any real index listing as the only backstop.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from typing import Any

from algotrading.core.config import StrikeSelectionConfig
from algotrading.infra.universe import (
    ChainSelection,
    bracket_dates,
    select_discovery_strikes,
    tenor_target_dates,
)


class CloseCaptureError(Exception):
    """A close capture that fetched contracts but kept none — a loud, non-silent failure.

    Raised when the snapshot returned option rows but every one was dropped (all post-``next_open``,
    i.e. a wrong-day capture), so the basket would land *zero* events. That is an anomaly, not a
    clean no-capture day: a genuinely optionless index returns ``None`` from
    ``collect_live_basket`` upstream of any snapshot (a labeled no-op). Surfacing this as a
    raised error makes the runner exit non-zero so the systemd ``OnFailure=`` alert fires, rather
    than silently landing an empty day that only an audit would later notice.
    """


class DiscoveryRunawayError(CloseCaptureError):
    """Discovery qualified an implausibly large strike window for one expiry — fail loud.

    The delta-driven discovery window is full-30Δ by policy (no strike cap — a cap would be the
    same intent-vs-delivery bound T-delta-window removed). Its only backstop is this runaway
    valve: if a single expiry's qualified strike count exceeds
    :data:`DISCOVERY_RUNAWAY_STRIKES_PER_EXPIRY` — far above any real index listing — the window
    is pathological (a degenerate listing, or a garbage spot/working vol) and the capture raises
    rather than streaming a runaway number of paced calls. It *raises*, never silently trims, so
    the failure is loud (the runner exits non-zero, ``OnFailure=`` alerts) instead of quietly
    capturing a malformed, oversized chain. A normal SPX/SX5E expiry lists ~100–135 strikes, so
    this never fires in normal operation.
    """


# The runaway guard is a pathology valve, NOT a cap: it raises rather than silently trimming, and
# is set so far above a real SPX/SX5E expiry (~135 listed strikes at the long end) that it never
# fires in normal operation — it only catches a degenerate listing or a garbage spot/vol that
# would otherwise qualify a runaway number of contracts.
DISCOVERY_RUNAWAY_STRIKES_PER_EXPIRY = 1000

# Fallback only: with no usable index spot there is no forward to delta-bound against, so the
# delta-driven window cannot be computed. We then keep a bounded near-the-money block centred on
# the median listed strike (deterministic, just not centred on the true forward) so discovery
# still yields a fittable, paced-safe slice rather than the whole ladder. This is a degraded path
# (the spot snapshot failed), logged as such — never the normal qualification.
DISCOVERY_FALLBACK_STRIKES_PER_SIDE = 16

# Floor on the discovery tenor: a month token's representative date can land on or before the
# trade date (a near-front month), which would make the working-vol window collapse to ~ATM. One
# day keeps the band non-degenerate and `select_strikes_delta_band`'s maturity validation happy.
_MIN_DISCOVERY_TENOR_DAYS = 1

_MONTH_TOKEN_ABBR: dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_month_token(token: str) -> date | None:
    """An IBKR ``MMMYY`` option-month token (e.g. ``DEC28``) → a mid-month representative date.

    The CP ``secdef/search`` lists option months as ``JUN26;JUL26;DEC28`` tokens; one token
    deflates to several concrete expiries downstream via ``secdef/info``. Mid-month (day 15) is
    the representative used to *bracket* tokens around a tenor target; the precise per-tenor
    bracket is refined on the concrete expiries by ``select_expiries_bracketing``. Returns
    ``None`` for an unparseable token (skipped, never guessed).
    """
    cleaned = token.strip().upper()
    if len(cleaned) != 5:
        return None
    month = _MONTH_TOKEN_ABBR.get(cleaned[:3])
    year = cleaned[3:]
    if month is None or not year.isdigit():
        return None
    return date(2000 + int(year), month, 15)


def select_discovery_months(months: Sequence[str], selection: ChainSelection) -> tuple[str, ...]:
    """Which listed month tokens to qualify into the chain.

    Legacy (no tenor targeting): the nearest ``max_expiries`` tokens in listed order — the old
    front-loaded slice. Tenor-targeted: the month tokens **straddling each pinned tenor's target
    date** (``bracket_dates`` over the tokens' representative dates), so discovery reaches the
    long end (2y/3y) the nearest-N slice silently dropped. Tokens that do not parse fall back to
    the legacy slice rather than being dropped, so a wire-shape surprise degrades safely.
    """
    if not selection.targets_tenors:
        return tuple(months[: selection.max_expiries])
    assert selection.as_of is not None  # targets_tenors guarantees this; pin it for the type
    parsed = [
        (parsed_date, token)
        for token in months
        if (parsed_date := parse_month_token(token)) is not None
    ]
    if not parsed:
        return tuple(months[: selection.max_expiries])
    targets = tenor_target_dates(selection.as_of, selection.tenor_years)
    kept = set(bracket_dates([parsed_date for parsed_date, _ in parsed], targets))
    return tuple(token for parsed_date, token in sorted(parsed) if parsed_date in kept)


def nearest_strikes(strikes: set[float], spot: float | None, per_side: int) -> list[float]:
    """The nearest-the-money block to qualify: up to ``per_side`` strikes either side of spot.

    The **fallback** qualification path, used only when there is no usable index spot (the spot
    snapshot failed) so the delta-driven window (``select_discovery_strikes``) cannot be
    computed — see :data:`DISCOVERY_FALLBACK_STRIKES_PER_SIDE`. Strikes are ranked by absolute
    distance to the centre and the nearest ``2 * per_side`` are kept, then returned ascending. The
    centre is the snapshot ``spot`` when usable; with no spot it falls back to the median listed
    strike — bounded and deterministic, just not centred on the true forward — mirroring
    ``chain_planning._strikes_by_moneyness``. A sparse name with fewer than ``2 * per_side`` listed
    strikes simply qualifies all of them.
    """
    positive = sorted({float(strike) for strike in strikes if float(strike) > 0.0})
    if not positive:
        return []
    if spot is not None and math.isfinite(spot) and spot > 0.0:
        centre = spot
    else:
        centre = positive[len(positive) // 2]
    nearest = sorted(positive, key=lambda strike: (abs(strike - centre), strike))[: 2 * per_side]
    return sorted(nearest)


def qualify_strikes_for_expiry(
    listed: set[float],
    *,
    month: str,
    spot: float | None,
    as_of: date,
    strike_selection: StrikeSelectionConfig,
    log: Any,
) -> list[float]:
    """The listed strikes discovery qualifies for ONE month token — the delta-driven window.

    The normal path computes the tenor (the month token's mid-month representative date minus
    the trade date, ACT/365, floored to a non-degenerate minimum) and qualifies the listed
    strikes that *contain* the 30Δ band at that tenor via ``select_discovery_strikes`` — a
    conservative-working-vol superset, full-30Δ with no cap, so the downstream economic
    selection reaches the true 30Δ put and call (the T-delta-window fix). It falls back to a
    bounded near-the-money block (:func:`nearest_strikes`), logged as a degraded path, only when
    there is no usable spot (no forward to delta-bound against) or the month token does not parse
    to a tenor. Raises :class:`DiscoveryRunawayError` when the qualified window is implausibly
    large — the fail-loud pacing valve, never a silent trim.
    """
    rep_date = parse_month_token(month)
    if rep_date is not None and spot is not None and math.isfinite(spot) and spot > 0.0:
        days = max((rep_date - as_of).days, _MIN_DISCOVERY_TENOR_DAYS)
        maturity_years = days / 365.0
        kept = list(
            select_discovery_strikes(
                listed,
                forward=spot,
                maturity_years=maturity_years,
                working_vol=strike_selection.discovery_working_vol,
                selection=strike_selection,
            )
        )
        log.info(
            "ibkr.close_capture.discovery_window",
            month=month,
            maturity_years=round(maturity_years, 4),
            working_vol=strike_selection.discovery_working_vol,
            listed=len(listed),
            kept=len(kept),
            strike_min=min(kept) if kept else None,
            strike_max=max(kept) if kept else None,
        )
    else:
        kept = nearest_strikes(listed, spot, DISCOVERY_FALLBACK_STRIKES_PER_SIDE)
        log.info(
            "ibkr.close_capture.discovery_window_fallback",
            month=month,
            reason="unparseable month token" if rep_date is None else "no usable spot",
            listed=len(listed),
            kept=len(kept),
        )
    if len(kept) > DISCOVERY_RUNAWAY_STRIKES_PER_EXPIRY:
        raise DiscoveryRunawayError(
            f"discovery qualified {len(kept)} strikes for {month} "
            f"(> {DISCOVERY_RUNAWAY_STRIKES_PER_EXPIRY}) — pathological listing or a bad "
            f"spot/working-vol; refusing to stream a runaway chain"
        )
    return kept
