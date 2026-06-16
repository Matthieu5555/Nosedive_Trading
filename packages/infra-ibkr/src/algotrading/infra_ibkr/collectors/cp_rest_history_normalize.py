from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime

from algotrading.core.provenance import ProvenanceStamp
from algotrading.infra.contracts import DailyBar
from pydantic import ValidationError

from .cp_rest_wire import HistoryBarRow

_TIME_MS = "t"


class HistoryNormalizeError(Exception):
    pass


def trade_date_of_bar(epoch_ms: object) -> date:
    if isinstance(epoch_ms, bool) or not isinstance(epoch_ms, (int, float)):
        raise HistoryNormalizeError(f"history bar timestamp must be numeric, got {epoch_ms!r}")
    if not math.isfinite(float(epoch_ms)):
        raise HistoryNormalizeError(f"history bar timestamp is not finite: {epoch_ms!r}")
    return datetime.fromtimestamp(float(epoch_ms) / 1000.0, tz=UTC).date()


def _bar_rejection(exc: ValidationError, row: Mapping[str, object]) -> HistoryNormalizeError:
    error = exc.errors()[0]
    location = error.get("loc", ())
    field = str(location[0]) if location else "<row>"
    if error.get("type") == "missing":
        return HistoryNormalizeError(f"history bar missing field {field!r}: {row!r}")
    return HistoryNormalizeError(f"history bar field {field!r} {error.get('msg', '')}: {row!r}")


def _row_to_bar(
    row: Mapping[str, object],
    *,
    provider: str,
    underlying: str,
    bar_type: str,
    source: str,
    provenance: ProvenanceStamp,
) -> DailyBar:
    try:
        bar = HistoryBarRow.model_validate(row)
    except ValidationError as exc:
        raise _bar_rejection(exc, row) from exc
    if bar.high < bar.low:
        raise HistoryNormalizeError(f"history bar high {bar.high!r} < low {bar.low!r}: {row!r}")
    for name, value in (("open", bar.open_price), ("close", bar.close)):
        if not (bar.low <= value <= bar.high):
            raise HistoryNormalizeError(
                f"history bar {name} {value!r} outside [low={bar.low!r}, high={bar.high!r}]: "
                f"{row!r}"
            )
    if bar.volume < 0:
        raise HistoryNormalizeError(f"history bar volume must be non-negative: {bar.volume!r}")
    return DailyBar(
        provider=provider,
        underlying=underlying,
        trade_date=trade_date_of_bar(bar.time_ms),
        open=bar.open_price,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        bar_type=bar_type,
        source=source,
        provenance=provenance,
    )


def history_to_daily_bars(
    payload: Mapping[str, object],
    *,
    provider: str,
    underlying: str,
    bar_type: str,
    source: str,
    provenance_for: object,
) -> tuple[DailyBar, ...]:
    data = payload.get("data")
    if data is None:
        return ()
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        raise HistoryNormalizeError(f"history payload 'data' must be a list, got {data!r}")
    bars: list[DailyBar] = []
    for row in data:
        if not isinstance(row, Mapping):
            raise HistoryNormalizeError(f"history bar must be a mapping, got {row!r}")
        trade_date = trade_date_of_bar(row.get(_TIME_MS))
        provenance = provenance_for(trade_date)  # type: ignore[operator]
        bars.append(
            _row_to_bar(
                row,
                provider=provider,
                underlying=underlying,
                bar_type=bar_type,
                source=source,
                provenance=provenance,
            )
        )
    return tuple(bars)
