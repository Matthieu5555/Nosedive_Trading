from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep, TradeDateDep
from ..serializers import SIGNAL_DISPLAY, strategy_signal_to_dict
from ..store_reads import latest_partition_date

router = APIRouter(prefix="/api/signals", tags=["signals"])

_TABLE = "strategy_signals"

_KIND_ORDER = {kind: index for index, kind in enumerate(SIGNAL_DISPLAY)}


def _kind_sort_key(kind: str) -> tuple[int, str]:
    return (_KIND_ORDER.get(kind, len(_KIND_ORDER)), kind)


def _signal_sort_key(row: object) -> tuple[int, str, str, str]:
    kind = getattr(row, "signal_kind", "")
    return (
        _KIND_ORDER.get(kind, len(_KIND_ORDER)),
        getattr(row, "subject", ""),
        getattr(row, "tenor_label", ""),
        kind,
    )


@router.get("/underlyings")
def list_signal_underlyings(ctx: CtxDep) -> JSONResponse:
    partitions = ctx.store.list_partitions(_TABLE)
    underlyings = sorted({underlying for _, underlying in partitions})
    return JSONResponse({"underlyings": underlyings})


def _empty(underlying: str, trade_date_iso: str | None) -> JSONResponse:
    return JSONResponse(
        {
            "underlying": underlying,
            "trade_date": trade_date_iso,
            "snapshot_ts": None,
            "n_signals": 0,
            "kinds": [],
            "signals": [],
            "by_kind": {},
        }
    )


@router.get("")
def get_signals(
    ctx: CtxDep,
    trade_date: TradeDateDep,
    underlying: str | None = None,
) -> JSONResponse:
    resolved_underlying = underlying or ctx.default_underlying

    resolved_date = trade_date or latest_partition_date(
        ctx.store.list_partitions(_TABLE), resolved_underlying
    )
    if resolved_date is None:
        return _empty(resolved_underlying, None)

    rows = [
        row
        for row in ctx.store.read(
            _TABLE, trade_date=resolved_date, underlying=resolved_underlying
        )
        if row.underlying == resolved_underlying
    ]
    if not rows:
        return _empty(resolved_underlying, resolved_date.isoformat())

    rows.sort(key=_signal_sort_key)
    signals = [strategy_signal_to_dict(row) for row in rows]

    by_kind: dict[str, list[dict[str, object]]] = {}
    for signal in signals:
        by_kind.setdefault(str(signal["signal_kind"]), []).append(signal)

    kinds = sorted(by_kind, key=_kind_sort_key)

    return JSONResponse(
        {
            "underlying": resolved_underlying,
            "trade_date": resolved_date.isoformat(),
            "snapshot_ts": signals[0]["snapshot_ts"],
            "n_signals": len(signals),
            "kinds": kinds,
            "signals": signals,
            "by_kind": by_kind,
        }
    )
