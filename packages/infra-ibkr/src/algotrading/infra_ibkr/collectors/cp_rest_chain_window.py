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
    pass


class DiscoveryRunawayError(CloseCaptureError):
    pass


DISCOVERY_RUNAWAY_STRIKES_PER_EXPIRY = 1000

DISCOVERY_FALLBACK_STRIKES_PER_SIDE = 16

_MIN_DISCOVERY_TENOR_DAYS = 1

_MONTH_TOKEN_ABBR: dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_month_token(token: str) -> date | None:
    cleaned = token.strip().upper()
    if len(cleaned) != 5:
        return None
    month = _MONTH_TOKEN_ABBR.get(cleaned[:3])
    year = cleaned[3:]
    if month is None or not year.isdigit():
        return None
    return date(2000 + int(year), month, 15)


def select_discovery_months(months: Sequence[str], selection: ChainSelection) -> tuple[str, ...]:
    if not selection.targets_tenors:
        return tuple(months[: selection.max_expiries])
    assert selection.as_of is not None
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
