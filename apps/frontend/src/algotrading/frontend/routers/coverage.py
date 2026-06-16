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
    return [
        (label, trade_date + timedelta(days=round(tenor_years(label) * 365.0)))
        for label in PINNED_TENORS
    ]


def _nearest_tenor(expiry: date, targets: list[tuple[str, date]]) -> str:
    return min(targets, key=lambda pair: abs((expiry - pair[1]).days))[0]


def _volume_by_expiry(snapshots: list) -> dict[str, float]:
    by_expiry: dict[str, float] = {}
    for snap in snapshots:
        volume = getattr(snap, "volume", None)
        if volume is None:
            continue
        parts = snap.instrument_key.split("|")
        if len(parts) < 7:
            continue
        expiry_seg = parts[6]
        if not expiry_seg:
            continue
        by_expiry[expiry_seg] = by_expiry.get(expiry_seg, 0.0) + volume
    return by_expiry


def _expiry_rows(
    masters: list,
    targets: list[tuple[str, date]],
    volume_by_expiry: dict[str, float] | None = None,
) -> list[dict[str, object]]:
    by_expiry: dict[date, list] = defaultdict(list)
    for master in masters:
        key = master.instrument
        if key.is_option() and key.expiry is not None and key.strike is not None:
            by_expiry[key.expiry].append(key)

    rows: list[dict[str, object]] = []
    for expiry in sorted(by_expiry):
        keys = by_expiry[expiry]
        strikes = {float(k.strike) for k in keys if k.strike is not None}
        expiry_iso = expiry.isoformat()
        total_vol: float | None = (
            volume_by_expiry.get(expiry_iso) if volume_by_expiry is not None else None
        )
        rows.append(
            {
                "expiry": expiry_iso,
                "tenor": _nearest_tenor(expiry, targets),
                "n_strikes": len(strikes),
                "n_calls": sum(1 for k in keys if (k.option_right or "").upper().startswith("C")),
                "n_puts": sum(1 for k in keys if (k.option_right or "").upper().startswith("P")),
                "strike_min": min(strikes) if strikes else None,
                "strike_max": max(strikes) if strikes else None,
                "total_volume": total_vol,
            }
        )
    return rows


def _tenor_coverage_rows(
    qc_rows: list, underlying: str
) -> tuple[list[dict[str, object]], str]:
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


def _check_status(qc_rows: list, check_name: str, underlying: str) -> str:
    for row in qc_rows:
        if row.check_name == check_name and row.target_key == underlying:
            return "fail" if str(row.qc_status).lower() in QC_FAIL_STATUSES else "pass"
    return "unknown"


@router.get("")
def get_coverage(
    ctx: CtxDep,
    trade_date: TradeDateDep,
    underlying: str | None = None,
    run_id: str | None = None,
) -> JSONResponse:
    # ``run_id`` pins coverage to the selected fetch; run-partitioned reads (snapshots, qc)
    # resolve that fetch's ``run=`` partition, while non-run-partitioned reads (instrument_master)
    # ignore it. Absent, every read resolves the newest fetch as before.
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
                "qc_status": "unknown",
                "delta_band_status": "unknown",
            }
        )

    masters = ctx.store.read(
        "instrument_master", trade_date=resolved_date, underlying=resolved_underlying
    )
    qc_rows = ctx.store.read("qc_results", trade_date=resolved_date, run_id=run_id)
    snapshots = ctx.store.read(
        "market_state_snapshots",
        trade_date=resolved_date,
        underlying=resolved_underlying,
        run_id=run_id,
    )

    targets = _tenor_targets(resolved_date)
    volume_by_expiry = _volume_by_expiry(snapshots)
    expiry_rows = _expiry_rows(masters, targets, volume_by_expiry)
    tenor_rows, _ = _tenor_coverage_rows(qc_rows, resolved_underlying)

    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "trade_date": resolved_date.isoformat(),
            "n_expiries": len(expiry_rows),
            "expiries": expiry_rows,
            "tenors": tenor_rows,
            "qc_status": _overall_qc_status(qc_rows, resolved_underlying),
            "delta_band_status": _check_status(qc_rows, _CHECK_DELTA_BAND, resolved_underlying),
        }
    )
