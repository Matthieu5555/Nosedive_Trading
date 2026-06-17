"""Build `RiskFreeRatePoint` rows from typed config + published levels, and `RateCurve` from rows.

`build_rate_points` takes the per-currency typed config (the pillar set + source conventions) and
the day's published levels keyed by pillar instrument, converts each to the canonical
continuous-ACT/365 zero rate (ADR 0054 RULED 4), and emits one provenance-stamped
`RiskFreeRatePoint` per pillar for the `as_of` date. `curve_from_points` rebuilds the `RateCurve`
evaluator from persisted rows — the caller filters rows to those published **as-of** the valuation
date (no look-ahead); this module evaluates only what it is handed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime

from algotrading.core.config import CurrencyRateConfig
from algotrading.core.provenance import (
    ProvenanceStamp,
    SourceRecordRef,
    snapshot_stamp,
)
from algotrading.infra.contracts import RatesDiagnostics, RiskFreeRatePoint

from .conventions import to_continuous_act365
from .curve import RateCurve

RATES_VERSION = "rates-1.0.0"

# The canonical internal day-count carried on every emitted point (ADR 0054 RULED 4).
CANONICAL_DAY_COUNT = "ACT/365"


class RateIngestError(ValueError):
    """A risk-free curve cannot be ingested from the given config + levels."""


def build_rate_points(
    *,
    currency_config: CurrencyRateConfig,
    published_levels: Mapping[str, float],
    as_of: date,
    snapshot_ts: datetime,
    source_snapshot_ts: datetime,
    calc_ts: datetime,
    config_hashes: Mapping[str, str],
    source_records: Sequence[SourceRecordRef] = (),
    quality_label: str = "good",
) -> tuple[RiskFreeRatePoint, ...]:
    """Convert published levels to canonical continuous-ACT/365 `RiskFreeRatePoint` rows.

    `published_levels` maps each pillar's `instrument` name to the rate the source published, in the
    source's own day-count/compounding. A pillar with no published level is **skipped** (a coverage
    gap, not a defect — the curve degrades to the pillars it has, mirroring the forward's gap rule).
    """
    points: list[RiskFreeRatePoint] = []
    for pillar in currency_config.pillars:
        if pillar.instrument not in published_levels:
            continue
        source_rate = published_levels[pillar.instrument]
        rate = to_continuous_act365(
            source_rate,
            pillar.maturity_years,
            source_day_count=currency_config.day_count,
            source_compounding=currency_config.compounding,
        )
        provenance: ProvenanceStamp = snapshot_stamp(
            calc_ts=calc_ts,
            code_version=RATES_VERSION,
            config_hashes=config_hashes,
            source_snapshot_ts=source_snapshot_ts,
            source_records=tuple(source_records),
            as_of=as_of,
        )
        diagnostics = RatesDiagnostics(
            source=currency_config.source,
            instrument=pillar.instrument,
            source_day_count=currency_config.day_count,
            source_compounding=currency_config.compounding,
            quality_label=quality_label,
        )
        points.append(
            RiskFreeRatePoint(
                as_of=as_of,
                currency=currency_config.currency,
                pillar_tenor=pillar.tenor_label,
                maturity_years=pillar.maturity_years,
                rate=rate,
                day_count=CANONICAL_DAY_COUNT,
                diagnostics=diagnostics,
                source_snapshot_ts=source_snapshot_ts,
                provenance=provenance,
            )
        )
    if not points:
        raise RateIngestError(
            f"no published level matched any pillar instrument for currency "
            f"{currency_config.currency!r}"
        )
    return tuple(points)


def curve_from_points(currency: str, points: Sequence[RiskFreeRatePoint]) -> RateCurve:
    """Build a `RateCurve` from persisted rows for one currency.

    The caller is responsible for the **as-of filter**: `points` must already be the rows published
    as-of the valuation date for `currency` (no look-ahead). Rows for other currencies are rejected
    so a mixed read cannot silently blend curves.
    """
    selected = [p for p in points if p.currency == currency]
    if not selected:
        raise RateIngestError(f"no risk-free rate points for currency {currency!r}")
    return RateCurve.from_pillars(
        currency, ((p.maturity_years, p.rate) for p in selected)
    )
