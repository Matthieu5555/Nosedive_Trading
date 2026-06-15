"""Coverage router: the captured option chain as a plain quality table (T-capture-coverage-panel).

The surface view smooths over gaps; this surfaces them. For one ``(underlying, trade_date)`` it
returns two already-on-disk facts, **no recompute**:

* **per-expiry capture** — from ``instrument_master``: how many strikes / calls / puts and the
  strike span actually captured at each listed expiry, each tagged with the pinned tenor it serves;
* **per-tenor coverage** — from ``qc_results`` (WS 1H's ``tenor_coverage_floor``): the whole pinned
  grid, so a tenor with **zero** captured expiries shows as a labeled zero-row, not an omission —
  the term-structure gap (e.g. 1m…3y empty) is then visible at a glance;
* **per-constituent capture outcomes** — from ``constituent_capture_outcomes`` (the widened S1
  capture lane's per-name ledger): for an index underlying, the labelled verdict
  (``captured`` / ``no_options`` / ``unentitled`` / ``unresolved``) of each of its heaviest
  constituents, so the entitlement question — *which* names return option chains on this account —
  is answered per name, never a silent absence.

Plus the date's overall QC verdict and the ``delta_band_completeness`` status (30Δ-band health).
``trade_date`` defaults to the latest date with ``instrument_master`` data, mirroring ``health``. A
missing partition yields a labeled empty payload (200, ``n_expiries == 0``), never a 500; a bad
``trade_date`` yields a 400. Reads only the requested date's partitions — no look-ahead.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta

from algotrading.infra.surfaces import PINNED_TENORS, tenor_years
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep, TradeDateDep
from ..store_reads import QC_FAIL_STATUSES, latest_partition_date

router = APIRouter(prefix="/api/coverage", tags=["coverage"])

_CHECK_TENOR_COVERAGE = "tenor_coverage_floor"
_CHECK_DELTA_BAND = "delta_band_completeness"


def _tenor_targets(trade_date: date) -> list[tuple[str, date]]:
    """Each pinned tenor's target date — ``trade_date + tenor·365`` (ACT/365), label-aligned.

    The same convention the capture selection and the projection use (``tenor_years``), so the
    tenor a captured expiry is tagged with here matches the tenor it actually serves downstream.
    """
    return [
        (label, trade_date + timedelta(days=round(tenor_years(label) * 365.0)))
        for label in PINNED_TENORS
    ]


def _nearest_tenor(expiry: date, targets: list[tuple[str, date]]) -> str:
    """The pinned tenor closest to ``expiry`` — a display tag, not the selection policy."""
    return min(targets, key=lambda pair: abs((expiry - pair[1]).days))[0]


def _expiry_rows(masters: list, targets: list[tuple[str, date]]) -> list[dict[str, object]]:
    """Per-expiry capture counts from the instrument masters, chronological."""
    by_expiry: dict[date, list] = defaultdict(list)
    for master in masters:
        key = master.instrument
        if key.is_option() and key.expiry is not None and key.strike is not None:
            by_expiry[key.expiry].append(key)

    rows: list[dict[str, object]] = []
    for expiry in sorted(by_expiry):
        keys = by_expiry[expiry]
        strikes = {float(k.strike) for k in keys if k.strike is not None}
        rows.append(
            {
                "expiry": expiry.isoformat(),
                "tenor": _nearest_tenor(expiry, targets),
                "n_strikes": len(strikes),
                "n_calls": sum(1 for k in keys if (k.option_right or "").upper().startswith("C")),
                "n_puts": sum(1 for k in keys if (k.option_right or "").upper().startswith("P")),
                "strike_min": min(strikes) if strikes else None,
                "strike_max": max(strikes) if strikes else None,
            }
        )
    return rows


def _tenor_coverage_rows(
    qc_rows: list, underlying: str
) -> tuple[list[dict[str, object]], str]:
    """Per-tenor coverage over the WHOLE pinned grid, plus the tenor-coverage check status.

    Drives off ``tenor_coverage_floor``'s ``breaching_tenors`` (which includes empty tenors as
    ``measured == 0`` rows). A tenor present in the breach list is ``fail`` with its measured/floor;
    any other pinned tenor cleared the floor (``pass``); with no check at all every tenor is
    ``unknown``. So the grid is always complete — an empty 1m…3y shows, it is never dropped.
    """
    breaches: dict[str, dict[str, object]] = {}
    status = "unknown"
    for row in qc_rows:
        if row.check_name == _CHECK_TENOR_COVERAGE and row.target_key == underlying:
            status = "fail" if str(row.qc_status).lower() in QC_FAIL_STATUSES else "pass"
            try:
                context = json.loads(row.context)
            except (TypeError, ValueError):
                context = {}
            for breach in context.get("breaching_tenors", []):
                tenor = breach.get("tenor")
                if tenor is not None:
                    breaches[str(tenor)] = breach
            break

    rows: list[dict[str, object]] = []
    for label in PINNED_TENORS:
        breach = breaches.get(label)
        if breach is not None:
            rows.append(
                {
                    "tenor": label,
                    "measured": breach.get("measured"),
                    "floor": breach.get("floor"),
                    "status": "fail",
                }
            )
        else:
            rows.append(
                {
                    "tenor": label,
                    "measured": None,
                    "floor": None,
                    "status": "pass" if status != "unknown" else "unknown",
                }
            )
    return rows, status


def _overall_qc_status(qc_rows: list, underlying: str) -> str:
    """The underlying's overall QC verdict — ``fail`` / ``pass`` / ``unknown``."""
    related = [
        row
        for row in qc_rows
        if row.target_key == underlying or str(row.target_key).startswith(underlying)
    ]
    if not related:
        return "unknown"
    if any(str(row.qc_status).lower() in QC_FAIL_STATUSES for row in related):
        return "fail"
    return "pass"


def _constituent_outcome_rows(outcomes: list, index: str) -> list[dict[str, object]]:
    """Per-constituent capture verdicts for one index, heaviest first (the entitlement ledger).

    Drives off ``constituent_capture_outcomes`` rows for ``index``: one labelled row per attempted
    constituent. Ordered by the recorded weight rank (ascending — rank 1 = heaviest), so the panel
    reads top-down exactly as the capture lane selected. A name's outcome is the captured verdict
    the lane recorded; ``n_options`` is the captured option-leg count (0 for a non-capture). With no
    ledger for this index/date (an index-only capture, or a date before the widened lane fired) the
    list is empty — the panel then simply shows no constituent section, never a fabricated row.
    """
    rows = [
        {
            "symbol": outcome.underlying,
            "rank": outcome.rank,
            "weight": outcome.weight,
            "outcome": outcome.outcome,
            "n_options": outcome.n_options,
            "detail": outcome.detail,
        }
        for outcome in outcomes
        if outcome.index == index
    ]
    rows.sort(key=lambda row: (row["rank"], row["symbol"]))
    return rows


def _check_status(qc_rows: list, check_name: str, underlying: str) -> str:
    """The status of one named check for ``underlying`` (``pass``/``fail``/``unknown``)."""
    for row in qc_rows:
        if row.check_name == check_name and row.target_key == underlying:
            return "fail" if str(row.qc_status).lower() in QC_FAIL_STATUSES else "pass"
    return "unknown"


@router.get("")
def get_coverage(
    ctx: CtxDep, trade_date: TradeDateDep, underlying: str | None = None
) -> JSONResponse:
    """Return the per-expiry capture + per-tenor QC coverage for one underlying and date."""
    resolved_underlying = underlying or ctx.default_underlying

    resolved_date = trade_date or latest_partition_date(
        ctx.store.list_partitions("instrument_master"), resolved_underlying
    )

    if resolved_date is None:
        return JSONResponse(
            {
                "underlying": resolved_underlying,
                "trade_date": None,
                "n_expiries": 0,
                "expiries": [],
                "tenors": [],
                "constituents": [],
                "qc_status": "unknown",
                "delta_band_status": "unknown",
            }
        )

    masters = ctx.store.read(
        "instrument_master", trade_date=resolved_date, underlying=resolved_underlying
    )
    qc_rows = ctx.store.read("qc_results", trade_date=resolved_date)
    outcomes = ctx.store.read("constituent_capture_outcomes", trade_date=resolved_date)

    targets = _tenor_targets(resolved_date)
    expiry_rows = _expiry_rows(masters, targets)
    tenor_rows, _ = _tenor_coverage_rows(qc_rows, resolved_underlying)
    constituent_rows = _constituent_outcome_rows(outcomes, resolved_underlying)

    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "trade_date": resolved_date.isoformat(),
            "n_expiries": len(expiry_rows),
            "expiries": expiry_rows,
            "tenors": tenor_rows,
            "constituents": constituent_rows,
            "qc_status": _overall_qc_status(qc_rows, resolved_underlying),
            "delta_band_status": _check_status(qc_rows, _CHECK_DELTA_BAND, resolved_underlying),
        }
    )
