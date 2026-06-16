from __future__ import annotations

import re
import zoneinfo
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from algotrading.infra.universe import CalendarResolver, parse_index_registry

_MIN_MARGIN_MINUTES = 5

_REPR_SESSION = date(2026, 6, 10)

_REGISTRY_BLOCK = {
    "SX5E": {
        "name": "EURO STOXX 50",
        "calendar": "XEUR",
        "currency": "EUR",
        "ibkr": {"conid": 1, "secType": "IND", "exchange": "EUREX"},
        "enabled": True,
    },
    "SPX": {
        "name": "S&P 500",
        "calendar": "XNYS",
        "currency": "USD",
        "ibkr": {"conid": 2, "secType": "IND", "exchange": "CBOE"},
        "enabled": True,
    },
}

_MIC_TO_SYMBOLS: dict[str, list[str]] = {
    "XEUR": ["SX5E"],
    "XNYS": ["SPX"],
}


def _timer_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "scripts" / "systemd"


def _find_timer_files() -> list[Path]:
    return sorted((_timer_dir()).glob("eod-capture@*.timer"))


def _extract_oncalendar(timer_content: str) -> str:
    for line in timer_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("OnCalendar="):
            return stripped[len("OnCalendar="):].strip()
    raise ValueError(f"No OnCalendar= line found in timer:\n{timer_content}")


def _parse_mic_from_filename(path: Path) -> str:
    m = re.fullmatch(r"eod-capture@([A-Z]+)\.timer", path.name)
    if not m:
        raise ValueError(f"Unexpected timer filename: {path.name}")
    return m.group(1)


def _fire_utc(on_calendar: str, on_date: date) -> datetime:
    parts = on_calendar.split()
    if len(parts) < 2:
        raise ValueError(f"Cannot parse OnCalendar: {on_calendar!r}")

    if len(parts) == 3:
        _, hhmm, tz_name = parts
    elif len(parts) == 2:
        _, hhmm = parts
        tz_name = "UTC"
    else:
        raise ValueError(f"Unrecognised OnCalendar format: {on_calendar!r}")

    fire_time = datetime.strptime(hhmm, "%H:%M").time()

    tz = zoneinfo.ZoneInfo(tz_name)
    fire_local = datetime.combine(on_date, fire_time, tzinfo=tz)
    return fire_local.astimezone(UTC)


def _timer_test_cases() -> list[tuple[Path, str]]:
    return [(p, _parse_mic_from_filename(p)) for p in _find_timer_files()]


@pytest.mark.parametrize(
    "timer_path,mic",
    _timer_test_cases(),
    ids=lambda x: x.name if isinstance(x, Path) else str(x),
)
def test_timer_fires_after_resolved_close(timer_path: Path, mic: str) -> None:
    registry = parse_index_registry(_REGISTRY_BLOCK)
    resolver = CalendarResolver(registry, as_of=_REPR_SESSION)

    content = timer_path.read_text()
    on_calendar = _extract_oncalendar(content)

    fire_utc = _fire_utc(on_calendar, _REPR_SESSION)

    symbols = _MIC_TO_SYMBOLS.get(mic)
    if symbols is None:
        pytest.skip(f"No test oracle for MIC {mic!r} — add it to _MIC_TO_SYMBOLS")

    for symbol in symbols:
        close_utc = resolver.session_close(symbol, _REPR_SESSION)

        assert fire_utc > close_utc, (
            f"{timer_path.name}: fire time {fire_utc.isoformat()} is NOT after "
            f"session close {close_utc.isoformat()} for {symbol} (MIC {mic}). "
            f"OnCalendar={on_calendar!r}. "
            "Re-run `uv run python scripts/gen_capture_timers.py` to fix."
        )

        margin = fire_utc - close_utc
        assert margin >= timedelta(minutes=_MIN_MARGIN_MINUTES), (
            f"{timer_path.name}: fire margin is only {margin} for {symbol} — "
            f"must be >= {_MIN_MARGIN_MINUTES} min."
        )


@pytest.mark.parametrize(
    "timer_path,mic",
    _timer_test_cases(),
    ids=lambda x: x.name if isinstance(x, Path) else str(x),
)
def test_timer_tracks_dst_correctly(timer_path: Path, mic: str) -> None:
    registry = parse_index_registry(_REGISTRY_BLOCK)
    winter_session = date(2026, 1, 9)
    resolver = CalendarResolver(registry, as_of=winter_session)

    content = timer_path.read_text()
    on_calendar = _extract_oncalendar(content)

    symbols = _MIC_TO_SYMBOLS.get(mic)
    if symbols is None:
        pytest.skip(f"No test oracle for MIC {mic!r}")

    fire_utc = _fire_utc(on_calendar, winter_session)

    for symbol in symbols:
        close_utc = resolver.session_close(symbol, winter_session)
        assert fire_utc > close_utc, (
            f"{timer_path.name}: fire time {fire_utc.isoformat()} is NOT after "
            f"winter session close {close_utc.isoformat()} for {symbol} (MIC {mic}). "
            f"OnCalendar={on_calendar!r}. "
            "The timer's timezone may be wrong — it must use the exchange timezone, not UTC."
        )
